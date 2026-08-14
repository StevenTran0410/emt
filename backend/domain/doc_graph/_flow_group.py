"""Business Flow Grouping for Phase 4 Big-Picture Business Flow Integrity (TICKET P4-1 & P4-1-FIX).

Groups fine-grained bd_flow_nodes/edges into named business flows, steps, and branches.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from domain.model_connector.types import ChatMessage, ChatRequest
from domain.business_flow_integrity._llm import call_with_reasoning_ladder
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.doc_graph._flow_prose import select_prose_chunks
from shared.utils import utc_now_iso

logger = logging.getLogger("codespectra.doc_graph.flow_group")

GROUP_PROMPT_VERSION = "p4_1_v2"
GROUP_SCHEMA_VERSION = "p4_1_v2"
_MAX_CONCURRENT_GROUPING = 5

BranchKind = Literal["SUCCESS", "FAILURE", "ERROR", "OTHER"]


class LLMStepSpec(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    aliases: list[str]
    name: str
    functionality: str


class LLMBranchSpec(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    edge_aliases: list[str]
    source_step_ix: int
    target_step_ix: int | None
    kind: BranchKind
    guard_description: str


class LLMFlowGroupResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    description: str
    steps: list[LLMStepSpec]
    branches: list[LLMBranchSpec]


_GROUP_SYSTEM_PROMPT = """You are given ONE business-flow diagram from a Business Design document plus related prose. Return the flow's business name, a 1-3 sentence description, a plain-language functionality description for each step (what it does in business terms — do NOT mention file names or line numbers in prose), and classify each branch as SUCCESS/FAILURE/ERROR/OTHER with a plain-English guard description. Reference steps/edges ONLY by the provided aliases. Respond ONLY in English. JSON only.

OUTPUT JSON SCHEMA:
{
  "name": "Flow Name",
  "description": "Flow description.",
  "steps": [
    {
      "aliases": ["N001"],
      "name": "Step Name",
      "functionality": "What step does."
    }
  ],
  "branches": [
    {
      "edge_aliases": ["E001"],
      "source_step_ix": 0,
      "target_step_ix": 1,
      "kind": "SUCCESS",
      "guard_description": "Condition description."
    }
  ]
}

EXAMPLE (shape only — never copy its names):
Input Nodes Registry:
N001 | DISP1 | step | Display initial menu
N002 | DEC1 | decision | Check user choice
N003 | EXEC1 | step | Execute selected program
Input Edges Registry:
E001 | N001 -> N002 | User clicks submit
E002 | N002 -> N003 | Choice is valid
Output JSON:
{
  "name": "Process Menu Selection",
  "description": "Receives user menu selection and routes to execution program.",
  "steps": [
    {"aliases": ["N001"], "name": "Display Menu", "functionality": "Renders user options on screen."},
    {"aliases": ["N002", "N003"], "name": "Validate & Execute Choice", "functionality": "Validates user selection and launches execution program."}
  ],
  "branches": [
    {"edge_aliases": ["E001"], "source_step_ix": 0, "target_step_ix": 1, "kind": "SUCCESS", "guard_description": "User clicks submit button."},
    {"edge_aliases": ["E002"], "source_step_ix": 1, "target_step_ix": 1, "kind": "SUCCESS", "guard_description": "Selection is valid."}
  ]
}

RULES:
1. Every qualifying step/decision/event/job_step node alias in the registry MUST be assigned to exactly one step (fold decision nodes into the step they guard). Do not omit any qualifying node alias.
2. edge_aliases reference E### from the Edges Registry. source_step_ix and target_step_ix are 0-based integer indexes into the steps array you return.
"""


def _clean_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = re.sub(r"<br\s*/?>", " ", label, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_candidate_key(node_id: str) -> str:
    """Extract candidate block key `{sub_ix}:{block_ix}` or `{sub_ix}:exseq:{table_ix}` from node id."""
    parts = node_id.split(":")
    if len(parts) <= 3:
        return "1:0"
    sub_ix = parts[3]
    if len(parts) > 4 and parts[4] == "exseq":
        table_ix = parts[5] if len(parts) > 5 else "0"
        return f"{sub_ix}:exseq:{table_ix}"
    if len(parts) > 4 and parts[4] == "evtab":
        return f"{sub_ix}:evtab"
    if len(parts) > 4:
        return f"{sub_ix}:{parts[4]}"
    return f"{sub_ix}:0"


def _extract_candidates(
    all_nodes: list[dict[str, Any]], all_edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group bd_flow_nodes by candidate block key and filter those with >=2 step/decision/event/job_step nodes."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for n in all_nodes:
        block_key = _parse_candidate_key(n["id"])
        groups.setdefault(block_key, []).append(n)

    candidates: list[dict[str, Any]] = []
    for block_key, n_list in groups.items():
        qualifying = [
            n for n in n_list if n.get("node_kind") in ("step", "decision", "event", "job_step")
        ]
        if len(qualifying) < 2:
            continue

        n_ids = {n["id"] for n in n_list}
        c_edges = [
            e for e in all_edges if e["src_node_id"] in n_ids and e["dst_node_id"] in n_ids
        ]

        doc_id = n_list[0]["doc_id"]
        sub_ix = 1
        try:
            sub_ix = int(n_list[0]["id"].split(":")[3])
        except (IndexError, ValueError):
            pass

        candidates.append({
            "block_key": block_key,
            "doc_id": doc_id,
            "sub_ix": sub_ix,
            "nodes": n_list,
            "edges": c_edges,
        })

    # Sort candidates by (sub_ix, block_key) for deterministic ordering
    candidates.sort(key=lambda c: (c["sub_ix"], c["block_key"]))
    for ix, c in enumerate(candidates):
        c["candidate_ix"] = ix + 1

    return candidates


def _attach_prose(candidate: dict[str, Any], parsed_docs: list[Any]) -> list[str]:
    """Find and attach prose chunks mentioning candidate's local_ids or bindings."""
    doc = next((d for d in parsed_docs if d.id == candidate["doc_id"]), None)
    if not doc:
        return []

    tokens = set()
    for n in candidate["nodes"]:
        if n.get("local_id"):
            tokens.add(n["local_id"])
        if n.get("binding"):
            tokens.add(n["binding"])

    chunks = select_prose_chunks(doc)
    attached_lines: list[str] = []

    for chunk in chunks:
        chunk_text = chunk.text
        if any(tok in chunk_text for tok in tokens if tok):
            for line_no, l_text in zip(range(chunk.line_start, chunk.line_end + 1), chunk_text.splitlines()):
                attached_lines.append(f"{line_no}: {l_text}")

    return attached_lines


def _build_fallback_flow(
    candidate: dict[str, Any], cluster_id: str, flow_ordinal: int
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build deterministic business flow, steps, and branches when LLM is disabled or fails."""
    block_key = candidate["block_key"]
    doc_id = candidate["doc_id"]
    sub_ix = candidate["sub_ix"]
    flow_id = f"bdbf:{cluster_id}:{doc_id}:{block_key.replace(':', '_')}"

    nodes = candidate["nodes"]
    edges = candidate["edges"]

    clean_labels = list({_clean_label(n.get("label") or n.get("binding")) for n in nodes if (n.get("label") or n.get("binding"))})
    name = f"Business flow {flow_ordinal} ({block_key})"
    description = f"Flow covering {', '.join(clean_labels[:5])}." if clean_labels else "Business flow processing sequence."

    flow_row = {
        "id": flow_id,
        "cluster_id": cluster_id,
        "doc_id": doc_id,
        "sub_ix": sub_ix,
        "block_key": block_key,
        "name": name,
        "description": description,
        "ordinal": flow_ordinal,
        "origin": "fallback",
        "model_id": None,
        "created_at": utc_now_iso(),
    }

    step_nodes = [n for n in nodes if n.get("node_kind") in ("step", "job_step", "event", "decision")]
    step_nodes.sort(key=lambda x: x.get("ordinal") or 0)

    step_rows: list[dict[str, Any]] = []
    node_to_step_id: dict[str, str] = {}

    for ix, n in enumerate(step_nodes):
        s_id = f"{flow_id}:s{ix + 1}"
        node_to_step_id[n["id"]] = s_id

        label_clean = _clean_label(n.get("label") or n.get("binding") or n.get("node_kind"))
        func = label_clean
        if n.get("guard_text"):
            func += f" (Condition: {_clean_label(n['guard_text'])})"

        step_rows.append({
            "id": s_id,
            "flow_id": flow_id,
            "name": label_clean or f"Step {ix + 1}",
            "functionality": func or "Executes step functionality.",
            "ordinal": ix + 1,
            "source_node_ids": json.dumps([n["id"]]),
            "doc_line_start": n.get("doc_line_start", 1),
            "doc_line_end": n.get("doc_line_end", 1),
            "created_at": utc_now_iso(),
        })

    branch_rows: list[dict[str, Any]] = []
    for b_ix, e in enumerate(edges):
        src_step = node_to_step_id.get(e["src_node_id"])
        dst_step = node_to_step_id.get(e["dst_node_id"])

        if not src_step:
            continue

        lbl = (e.get("label") or "") + " " + (e.get("guard_text") or "")
        kind: BranchKind = "OTHER"
        if "✅" in lbl or "success" in lbl.lower() or "ok" in lbl.lower():
            kind = "SUCCESS"
        elif "❌" in lbl or "fail" in lbl.lower():
            kind = "FAILURE"
        elif "error" in lbl.lower() or "except" in lbl.lower():
            kind = "ERROR"

        branch_rows.append({
            "id": f"{flow_id}:b{b_ix + 1}",
            "flow_id": flow_id,
            "source_step_id": src_step,
            "target_step_id": dst_step,
            "branch_kind": kind,
            "guard_description": _clean_label(e.get("guard_text") or e.get("label") or "Default transition"),
            "source_edge_ids": json.dumps([e["id"]]),
            "created_at": utc_now_iso(),
        })

    return flow_row, step_rows, branch_rows


def _validate_llm_grouping(
    validated: LLMFlowGroupResponse,
    node_alias_map: dict[str, str],
    edge_alias_map: dict[str, str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Hard-validate LLM grouping response aliases and index bounds."""
    used_node_aliases: set[str] = set()

    for s in validated.steps:
        for a in s.aliases:
            if a not in node_alias_map:
                logger.warning(f"[flow_group] LLM referenced unknown node alias: {a}")
                return False
            if a in used_node_aliases:
                logger.warning(f"[flow_group] LLM node alias used multiple times: {a}")
                return False
            used_node_aliases.add(a)

    qualifying_aliases = {
        alias for alias, nid in node_alias_map.items()
        if nodes_by_id.get(nid, {}).get("node_kind") in ("step", "decision", "event", "job_step")
    }

    if not qualifying_aliases.issubset(used_node_aliases):
        logger.warning(
            f"[flow_group] LLM missed some qualifying node aliases: used {len(used_node_aliases & qualifying_aliases)} / {len(qualifying_aliases)}"
        )
        return False

    n_steps = len(validated.steps)
    for b in validated.branches:
        if not (0 <= b.source_step_ix < n_steps):
            logger.warning(f"[flow_group] LLM branch source_step_ix out of bounds: {b.source_step_ix}")
            return False
        if b.target_step_ix is not None and not (0 <= b.target_step_ix < n_steps):
            logger.warning(f"[flow_group] LLM branch target_step_ix out of bounds: {b.target_step_ix}")
            return False
        for ea in b.edge_aliases:
            if ea not in edge_alias_map:
                logger.warning(f"[flow_group] LLM referenced unknown edge alias: {ea}")
                return False

    return True


async def run_bd_flow_grouping(
    db: Any,
    parsed_docs: list[Any],
    cluster_id: str,
    llm_enabled: bool = True,
    provider_id: str | None = None,
) -> None:
    """Group BD flow nodes/edges into Business Flows, steps, and branches (TICKET P4-1 & P4-1-FIX)."""
    async with db.execute(
        "SELECT id, doc_id, node_kind, local_id, binding, binding_type, label, ordinal, guard_text, doc_line_start, doc_line_end FROM bd_flow_nodes WHERE cluster_id=? ORDER BY id",
        (cluster_id,),
    ) as cur:
        all_nodes = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT id, doc_id, src_node_id, dst_node_id, edge_kind, label, guard_text, doc_line FROM bd_flow_edges WHERE cluster_id=? ORDER BY id",
        (cluster_id,),
    ) as cur:
        all_edges = [dict(r) for r in await cur.fetchall()]

    if not all_nodes:
        return

    await db.execute("DELETE FROM bd_business_steps WHERE flow_id IN (SELECT id FROM bd_business_flows WHERE cluster_id=?)", (cluster_id,))
    await db.execute("DELETE FROM bd_business_branches WHERE flow_id IN (SELECT id FROM bd_business_flows WHERE cluster_id=?)", (cluster_id,))
    await db.execute("DELETE FROM bd_business_flows WHERE cluster_id=?", (cluster_id,))
    await db.commit()

    candidates = _extract_candidates(all_nodes, all_edges)
    if not candidates:
        return

    sem = asyncio.Semaphore(_MAX_CONCURRENT_GROUPING)

    async def process_candidate(candidate: dict[str, Any], flow_ordinal: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        fallback_res = _build_fallback_flow(candidate, cluster_id, flow_ordinal)
        if not llm_enabled or not provider_id:
            return fallback_res

        node_alias_map: dict[str, str] = {}
        node_lines: list[str] = []
        nodes_by_id = {n["id"]: n for n in candidate["nodes"]}

        for i, n in enumerate(candidate["nodes"]):
            alias = f"N{i + 1:03d}"
            node_alias_map[alias] = n["id"]
            lbl = _clean_label(n.get("label") or n.get("binding"))
            node_lines.append(f"{alias} | {n.get('binding') or ''} | {n['node_kind']} | {lbl}")

        edge_alias_map: dict[str, str] = {}
        edge_lines: list[str] = []
        reverse_node_alias = {v: k for k, v in node_alias_map.items()}
        for i, e in enumerate(candidate["edges"]):
            alias = f"E{i + 1:03d}"
            edge_alias_map[alias] = e["id"]
            src_a = reverse_node_alias.get(e["src_node_id"], "N???")
            dst_a = reverse_node_alias.get(e["dst_node_id"], "N???")
            gt = _clean_label(e.get("guard_text") or e.get("label"))
            edge_lines.append(f"{alias} | {src_a} -> {dst_a} | {gt}")

        attached_prose = _attach_prose(candidate, parsed_docs)
        prose_payload = "\n".join(attached_prose) if attached_prose else "No attached prose."

        user_content = (
            f"Candidate Block: {candidate['block_key']}\n"
            f"Nodes Registry:\n" + "\n".join(node_lines) + "\n\n"
            f"Edges Registry:\n" + "\n".join(edge_lines) + "\n\n"
            f"Attached Prose:\n{prose_payload}"
        )

        payload_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        cache_key = f"grouping:{GROUP_PROMPT_VERSION}:{GROUP_SCHEMA_VERSION}:{candidate['doc_id']}:{candidate['block_key']}:{provider_id}:{payload_hash}"

        async with db.execute(
            "SELECT status, response_json FROM bd_flow_llm_cache WHERE cache_key=?", (cache_key,)
        ) as cur:
            row = await cur.fetchone()

        def _build_req(effort: str) -> ChatRequest:
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_GROUP_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user_content),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse_group(text: str) -> LLMFlowGroupResponse:
            parsed_json = _parse_llm_json(text)
            validated = LLMFlowGroupResponse.model_validate(parsed_json)
            if not _validate_llm_grouping(validated, node_alias_map, edge_alias_map, nodes_by_id):
                raise ValueError("LLM grouping response failed alias/index validation")
            return validated

        if row and row["status"] == "ok":
            try:
                validated = _parse_group(row["response_json"])
            except (ValueError, ValidationError) as ve:
                logger.warning(f"[flow_group] Cached LLM response failed validation for candidate {candidate['block_key']}: {ve}; fallback used.")
                return fallback_res
        else:
            # Judgment task (business flow grouping) — open at high reasoning; one low-effort retry
            # on empty content or schema/pydantic validation failure (Ticket P5-0).
            async with sem:
                ladder_res = await call_with_reasoning_ladder(
                    _build_req, _parse_group, first_effort="high",
                    label=f"[flow_group] candidate {candidate['block_key']}",
                )
            if ladder_res is None:
                return fallback_res
            validated, raw_text = ladder_res
            await db.execute(
                "INSERT OR REPLACE INTO bd_flow_llm_cache (cache_key, response_json, status, created_at) VALUES (?, ?, 'ok', ?)",
                (cache_key, raw_text, utc_now_iso()),
            )
            await db.commit()

        try:
            flow_id = f"bdbf:{cluster_id}:{candidate['doc_id']}:{candidate['block_key'].replace(':', '_')}"
            flow_row = {
                "id": flow_id,
                "cluster_id": cluster_id,
                "doc_id": candidate["doc_id"],
                "sub_ix": candidate["sub_ix"],
                "block_key": candidate["block_key"],
                "name": validated.name,
                "description": validated.description,
                "ordinal": flow_ordinal,
                "origin": "llm",
                "model_id": provider_id,
                "created_at": utc_now_iso(),
            }

            step_rows: list[dict[str, Any]] = []
            step_id_map: dict[int, str] = {}

            for s_ix, s_spec in enumerate(validated.steps):
                s_id = f"{flow_id}:s{s_ix + 1}"
                step_id_map[s_ix] = s_id

                source_node_ids = [node_alias_map[a] for a in s_spec.aliases if a in node_alias_map]
                doc_lines = [nodes_by_id[nid].get("doc_line_start", 1) for nid in source_node_ids if nid in nodes_by_id]
                line_start = min(doc_lines) if doc_lines else 1
                line_end = max(doc_lines) if doc_lines else 1

                step_rows.append({
                    "id": s_id,
                    "flow_id": flow_id,
                    "name": s_spec.name,
                    "functionality": s_spec.functionality,
                    "ordinal": s_ix + 1,
                    "source_node_ids": json.dumps(source_node_ids),
                    "doc_line_start": line_start,
                    "doc_line_end": line_end,
                    "created_at": utc_now_iso(),
                })

            branch_rows: list[dict[str, Any]] = []
            for b_ix, b_spec in enumerate(validated.branches):
                src_step_id = step_id_map[b_spec.source_step_ix]
                dst_step_id = step_id_map[b_spec.target_step_ix] if b_spec.target_step_ix is not None else None
                source_edge_ids = [edge_alias_map[ea] for ea in b_spec.edge_aliases if ea in edge_alias_map]

                branch_rows.append({
                    "id": f"{flow_id}:b{b_ix + 1}",
                    "flow_id": flow_id,
                    "source_step_id": src_step_id,
                    "target_step_id": dst_step_id,
                    "branch_kind": b_spec.kind,
                    "guard_description": b_spec.guard_description,
                    "source_edge_ids": json.dumps(source_edge_ids),
                    "created_at": utc_now_iso(),
                })

            return flow_row, step_rows, branch_rows

        except (ValueError, ValidationError) as ve:
            logger.warning(f"[flow_group] LLM JSON parse/validation error for candidate {candidate['block_key']}: {ve}")
            return fallback_res

    tasks = [process_candidate(c, ix + 1) for ix, c in enumerate(candidates)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_flow_rows: list[dict[str, Any]] = []
    all_step_rows: list[dict[str, Any]] = []
    all_branch_rows: list[dict[str, Any]] = []

    for ix, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning(f"[flow_group] Candidate processing failed with exception: {res}")
            f_row, s_rows, b_rows = _build_fallback_flow(candidates[ix], cluster_id, ix + 1)
        else:
            f_row, s_rows, b_rows = res

        all_flow_rows.append(f_row)
        all_step_rows.extend(s_rows)
        all_branch_rows.extend(b_rows)

    if all_flow_rows:
        flow_tuples = [
            (
                r["id"], r["cluster_id"], r["doc_id"], r["sub_ix"], r["block_key"],
                r["name"], r["description"], r["ordinal"], r["origin"], r["model_id"], r["created_at"],
            )
            for r in all_flow_rows
        ]
        await db.executemany(
            """
            INSERT INTO bd_business_flows
            (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, model_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            flow_tuples,
        )

    if all_step_rows:
        step_tuples = [
            (
                r["id"], r["flow_id"], r["name"], r["functionality"], r["ordinal"],
                r["source_node_ids"], r["doc_line_start"], r["doc_line_end"], r["created_at"],
            )
            for r in all_step_rows
        ]
        await db.executemany(
            """
            INSERT INTO bd_business_steps
            (id, flow_id, name, functionality, ordinal, source_node_ids, doc_line_start, doc_line_end, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            step_tuples,
        )

    if all_branch_rows:
        branch_tuples = [
            (
                r["id"], r["flow_id"], r["source_step_id"], r["target_step_id"],
                r["branch_kind"], r["guard_description"], r["source_edge_ids"], r["created_at"],
            )
            for r in all_branch_rows
        ]
        await db.executemany(
            """
            INSERT INTO bd_business_branches
            (id, flow_id, source_step_id, target_step_id, branch_kind, guard_description, source_edge_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            branch_tuples,
        )

    await db.commit()
    logger.info(f"[flow_group] Created {len(all_flow_rows)} business flows for cluster {cluster_id}")
