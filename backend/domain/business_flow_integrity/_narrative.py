"""Grounded per-flow LLM narrative generation for Phase 4 Part 4 (TICKET P4-4)."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from pydantic import BaseModel, ConfigDict

from domain.business_flow_integrity._llm import call_with_reasoning_ladder
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.utils import new_id, utc_now_iso

logger = logging.getLogger("codespectra.bfi.narrative")


async def _persist_flow_narratives(
    db: Any, cluster_id: str, snapshot_id: str, narratives: dict[str, str], origins: dict[str, str]
) -> None:
    """Store per-flow narratives so a machine without LLM access can read them back (delete+insert)."""
    await db.execute(
        "DELETE FROM business_flow_llm_output WHERE cluster_id=? AND snapshot_id=? AND kind='narrative'",
        (cluster_id, snapshot_id),
    )
    now = utc_now_iso()
    rows = [
        (new_id(), cluster_id, snapshot_id, "narrative", fid, text, origins.get(fid, "fallback"), now)
        for fid, text in narratives.items()
    ]
    if rows:
        await db.executemany(
            "INSERT INTO business_flow_llm_output (id, cluster_id, snapshot_id, kind, ref_id, content, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    await db.commit()


async def load_flow_narratives(db: Any, cluster_id: str, snapshot_id: str) -> dict[str, str]:
    """Read persisted per-flow narratives (flow_id -> narrative). Empty dict if none stored."""
    async with db.execute(
        "SELECT ref_id, content FROM business_flow_llm_output WHERE cluster_id=? AND snapshot_id=? AND kind='narrative'",
        (cluster_id, snapshot_id),
    ) as cur:
        return {r["ref_id"]: r["content"] for r in await cur.fetchall()}

# In-memory content-hash cache: identical deterministic batch payloads (same flow facts) skip
# a repeat LLM call. Only successful parses are cached — never empty/transport failures.
_narrative_batch_cache: dict[str, dict[str, str]] = {}


class FlowNarrativeItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    flow_id: str
    narrative: str


class FlowNarrativesResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    narratives: list[FlowNarrativeItem]


_NARRATIVE_SYSTEM_PROMPT = """You are a technical documentation analyst. For each given business flow, write ONE clear, grounded conclusion paragraph summarizing its source code implementation status for a non-technical manager.

Rules:
1. Rely ONLY on the provided deterministic facts (flow name/description, step names, verdicts, mapped program chains, reasons).
2. Articulate the facts cleanly — do NOT decide or invent any match or file name.
3. Mention how many steps are backed, cite key program call chains (e.g., HSBMENU5.pfd -> PHNIXLOT.clist), and note any missing or contradicted steps.
4. Keep each narrative to one concise paragraph (2-4 sentences).
5. Respond ONLY in English.
6. Output MUST strictly match the JSON schema { "narratives": [ { "flow_id": "...", "narrative": "..." } ] }.
"""


def _generate_fallback_flow_narrative(
    flow_id: str, flow_name: str, steps: list[dict[str, Any]], branches: list[dict[str, Any]]
) -> str:
    """Generate a deterministic, concrete narrative naming mapped steps/bindings and missing steps (no bare counts)."""
    matched_steps = [s for s in steps if s.get("verdict") == "MATCH"]
    unmatched_steps = [s for s in steps if s.get("verdict") != "MATCH"]

    m_cnt = len(matched_steps)
    tot = len(steps)

    matched_summary_parts = []
    if matched_steps:
        for ms in matched_steps[:2]:
            bindings = ms.get("bindings") or []
            b_str = " -> ".join(bindings) if bindings else "source code"
            matched_summary_parts.append(f"'{ms.get('name', 'Step')}' ({b_str})")
        matched_str = f"Key steps such as {', '.join(matched_summary_parts)} are verified in source code."
    else:
        matched_str = "No business steps are currently traced to source code."

    unmatched_summary_parts = []
    if unmatched_steps:
        for us in unmatched_steps[:2]:
            unmatched_summary_parts.append(f"'{us.get('name', 'Step')}'")
        unmatched_str = f"Steps including {', '.join(unmatched_summary_parts)} remain untraced or missing from source."
    else:
        unmatched_str = "All documented business steps are fully backed by source code."

    return f"Flow '{flow_name}' is {m_cnt}/{tot} steps backed. {matched_str} {unmatched_str}".strip()


async def generate_flow_narratives(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> dict[str, str]:
    """Generate grounded LLM narratives for each business flow in batches of <=5, or fallback deterministically."""
    # 1. Fetch business flows
    async with db.execute(
        "SELECT id, name, description, block_key, sub_ix FROM bd_business_flows WHERE cluster_id=? ORDER BY ordinal ASC",
        (cluster_id,),
    ) as cur:
        b_flows = [dict(r) for r in await cur.fetchall()]

    if not b_flows:
        return {}

    flow_ids = [f["id"] for f in b_flows]
    f_str = ",".join("?" for _ in flow_ids)

    # 2. Fetch steps & branches
    b_steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    async with db.execute(
        f"SELECT id, flow_id, name, functionality, ordinal FROM bd_business_steps WHERE flow_id IN ({f_str}) ORDER BY ordinal ASC",
        flow_ids,
    ) as cur:
        for r in await cur.fetchall():
            b_steps_by_flow.setdefault(r["flow_id"], []).append(dict(r))

    b_branches_by_flow: dict[str, list[dict[str, Any]]] = {}
    async with db.execute(
        f"SELECT id, flow_id, branch_kind, guard_description FROM bd_business_branches WHERE flow_id IN ({f_str})",
        flow_ids,
    ) as cur:
        for r in await cur.fetchall():
            b_branches_by_flow.setdefault(r["flow_id"], []).append(dict(r))

    # 3. Fetch unit verdicts
    async with db.execute(
        "SELECT unit_id, verdict, reason, route_segment_json FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?",
        (cluster_id, snapshot_id),
    ) as cur:
        uv_map = {r["unit_id"]: dict(r) for r in await cur.fetchall()}

    narratives: dict[str, str] = {}
    origins: dict[str, str] = {}

    # Assemble structured payload data per flow
    flow_payloads: list[dict[str, Any]] = []
    for flow in b_flows:
        fid = flow["id"]
        steps = b_steps_by_flow.get(fid, [])
        branches = b_branches_by_flow.get(fid, [])

        step_details = []
        for st in steps:
            uv = uv_map.get(st["id"], {})
            bindings = []
            if uv.get("route_segment_json"):
                try:
                    seg = json.loads(uv["route_segment_json"])
                    bindings = seg.get("bindings", [])
                except Exception:
                    pass
            step_details.append({
                "id": st["id"],
                "name": st["name"],
                "prose": st["functionality"],
                "verdict": uv.get("verdict", "UNKNOWN"),
                "reason": uv.get("reason", "Not evaluated"),
                "bindings": bindings,
            })

        branch_details = []
        for br in branches:
            uv = uv_map.get(br["id"], {})
            branch_details.append({
                "id": br["id"],
                "branch_kind": br["branch_kind"],
                "guard": br["guard_description"],
                "verdict": uv.get("verdict", "UNKNOWN"),
                "reason": uv.get("reason", "Not evaluated"),
            })

        flow_payloads.append({
            "flow_id": fid,
            "flow_name": flow["name"],
            "description": flow.get("description"),
            "steps": step_details,
            "branches": branch_details,
        })

        # Pre-populate fallback
        narratives[fid] = _generate_fallback_flow_narrative(
            fid, flow["name"], step_details, branch_details
        )
        origins[fid] = "fallback"

    if not provider_id:
        return narratives

    # Batch LLM calls (<=5 flows per batch)
    BATCH_SIZE = 5
    for i in range(0, len(flow_payloads), BATCH_SIZE):
        batch = flow_payloads[i : i + BATCH_SIZE]
        batch_json = json.dumps(batch, sort_keys=True)
        batch_hash = hashlib.sha256(batch_json.encode("utf-8")).hexdigest()

        cached = _narrative_batch_cache.get(batch_hash)
        if cached is not None:
            narratives.update(cached)
            continue

        prompt = (
            "Write a grounded one-paragraph conclusion narrative for each of the following business flows:\n"
            + batch_json
        )

        def _build_req(effort: str) -> ChatRequest:
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_NARRATIVE_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=prompt),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse_batch(text: str) -> dict[str, str]:
            # Strip backticks if any
            stripped = text.strip()
            if stripped.startswith("```"):
                lines = stripped.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                stripped = "\n".join(lines).strip()

            parsed = json.loads(stripped)
            validated = FlowNarrativesResponse.model_validate(parsed)
            return {
                item.flow_id: item.narrative.strip()
                for item in validated.narratives
                if item.narrative and item.narrative.strip()
            }

        # Prose task (no judgment) — open at low reasoning; one same-tier retry on empty/invalid output.
        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse_batch, first_effort="low", label=f"BFI Flow Narrative batch {i}"
        )
        if ladder_res is None:
            continue
        batch_results, _raw_text = ladder_res
        if batch_results:
            narratives.update(batch_results)
            for updated_fid in batch_results:
                origins[updated_fid] = "llm"
            _narrative_batch_cache[batch_hash] = batch_results

    # This was a real generation pass (provider set) — persist so an LLM-blocked machine reads it back.
    await _persist_flow_narratives(db, cluster_id, snapshot_id, narratives, origins)

    return narratives
