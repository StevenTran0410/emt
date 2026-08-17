"""Phase U Stage 4: Verdict Verifier-Corrector + Fusion + Flow Rollup (TICKET U4).

Judges the final user-flow verdicts:
1. Deterministic pre-pass:
   - in_scope=0 -> OUT_OF_SCOPE / scope divergence (0 LLM calls).
   - presentation=True -> candidate UNVERIFIABLE passed as prior to verifier.
2. LLM#4 Verifier-corrector (evaluate_verdict_batch, batch <= 5, LOW/derived reasoning):
   - Re-judges step from code snippets + anchor proposals + BD proposals.
   - Emits COVERED | BD_MISSING | CONTRADICTED | UNVERIFIABLE + kept_bd_ids + citations + corrected flag.
3. Deterministic fusion & gating:
   - Re-fetches cited lines via resolve_citation + window containment against shown snippets.
   - Invalid citation -> UNVERIFIABLE (CITATION_INVALID / CITATION_OUT_OF_WINDOW).
   - BD_MISSING / CONTRADICTED require >= 1 valid citation.
   - COVERED requires valid citation (basis: 'own_citation') or mapped BD unit with MATCH/PARTIAL (basis: 'bd_verdict').
   - LLM_NO_RESPONSE -> UNVERIFIABLE.
4. BD-side verdicts:
   - Unreferenced BD units -> BD_EXTRA (side='bd').
   - Mapped BD units -> COVERED (side='bd').
5. Deterministic flow-level rollup (§3b):
   - Computes MATCHED | PARTIAL | DIVERGENT | UNCOVERED | OUT_OF_SCOPE per flow.
   - Persists user_verdicts (step, flow, bd) + step verdict artifacts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._citation import ResolvedCitation, resolve_citation
from domain.business_flow_integrity._llm import (
    LLM_CONCURRENCY,
    assert_exact_id_coverage,
    call_with_reasoning_ladder,
    coerce_results_wrapper,
)
from domain.business_flow_integrity._retrieval import Snippet
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.logger import logger
from shared.utils import new_id, utc_now_iso

from ._anchor import (
    SeedHit,
    resolve_step_files,
    retrieve_step_snippets,
    seed_literal_hits,
)
from ._mapping import BDContext, BDUnit, load_bd_context


_VERDICT_BATCH_SIZE = 5

_VERDICT_SYSTEM_PROMPT = """You are the FINAL verifier and corrector for user-flow alignment between customer business flows, \
AI-generated Business Design (BD) documents, and legacy mainframe source code.

For each user step, you are given:
- Step details: text_ja, text_en, kind, trigger, expected, section_id, screen_name_ja
- anchor_result: Upstream code anchor proposal (citations, fetched verbatim text, reason) — unverified hypothesis
- bd_mappings: Upstream BD mapping proposals (mapped BD units, functionality, BD<->code verdict, confidence, reason) — unverified hypothesis
- snippets: The real candidate source code snippets shown for this step

Treat upstream proposals as UNVERIFIED HYPOTHESES. Upstream agents may be wrong:
- The anchor matcher may have mis-cited or missed a contradicting line in the snippets.
- The BD mapper may have mapped to an irrelevant BD unit or missed a relevant one.
Re-judge strictly from the evidence shown. If an upstream output is wrong, CORRECT it:
set `corrected: true`, output your OWN kept_bd_ids, your OWN citations, and explain in `reason`.

Exact Verdict per user step (EXACTLY one of):
- COVERED: >=1 BD mapping is genuinely about this behavior AND code evidence (valid citation in snippets) supports the same outcome.
- BD_MISSING: Code evidence supports the user step (valid citation required) but NO BD unit describes it (e.g. a user-visible dialog the BD collapsed). Cite the code lines.
- CONTRADICTED: A BD unit and user step assert DIFFERENT outcomes for the same behavior (e.g. BD says 'retry' vs user says 'show error'), OR code (cited) contradicts the user claim. Requires a positive conflict. Name the conflicting BD unit and/or cite the conflicting line.
- UNVERIFIABLE: Cannot confirm nor refute from available evidence (includes presentation claims: layout/color/font; includes cases where snippets are missing or insufficient). Prefer over guessing.

Divergence field:
- "scope": when the gap is explained by explicit scope exclusions (e.g. step text or notes say 'PoC: option 1 only' or 'out of scope').
- "behavioural": for BD_MISSING or CONTRADICTED when not explained by scope.
- null: for COVERED or UNVERIFIABLE.

Citations:
- Citations are LOCATIONS ONLY (rel_path, line_start, line_end) pointing inside the provided snippets. The system re-fetches the verbatim text.
- kept_bd_ids: list of BD unit aliases (e.g. ["bd1"]) from the proposals/catalog that genuinely describe this step. If none survive, emit [].

Output schema:
Respond ONLY with a JSON object:
{
  "results": [
    {
      "unit_id": "u1",
      "verdict": "COVERED",
      "divergence": null,
      "kept_bd_ids": ["bd1"],
      "citations": [{"rel_path": "HSBMENU5.pfd", "line_start": 12, "line_end": 18}],
      "corrected": false,
      "reason": "..."
    }
  ]
}
Every unit_id must appear exactly once.
"""


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class LLMCitation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rel_path: str
    line_start: int
    line_end: int


class LLMVerdictItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    unit_id: str
    verdict: str  # COVERED | BD_MISSING | CONTRADICTED | UNVERIFIABLE
    divergence: str | None = None  # scope | behavioural | None
    kept_bd_ids: list[str] = Field(default_factory=list)
    citations: list[LLMCitation] = Field(default_factory=list)
    corrected: bool = False
    reason: str = ""


class LLMVerdictBatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[LLMVerdictItem]


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class StepVerdictContext:
    user_step_id: str
    step_alias: str
    flow_id: str
    ordinal: int
    kind: str
    section_id: str | None
    text_ja: str
    text_en: str | None
    trigger_ja: str | None
    expected_ja: str | None
    screen_name_ja: str | None
    in_scope: bool
    scope_note: str | None
    presentation: bool
    snippets: list[Snippet]
    snippet_id_map: dict[str, Snippet]
    anchor_proposals: list[dict[str, Any]]
    bd_proposals: list[dict[str, Any]]
    bd_alias_map: dict[str, dict[str, Any]]
    bd_id_to_alias: dict[str, str]


@dataclass
class VerdictRunSummary:
    doc_id: str
    cluster_id: str
    snapshot_id: str
    run_id: str
    total_steps: int
    in_scope_steps: int
    covered_steps: int
    bd_missing_steps: int
    contradicted_steps: int
    unverifiable_steps: int
    out_of_scope_steps: int
    bd_extra_count: int
    flow_verdicts: dict[str, str]
    step_verdicts: dict[str, str]
    activity_verdicts: dict[str, str] = field(default_factory=dict)
    llm_batches_issued: int = 0
    physical_attempts: int = 0


# ---------------------------------------------------------------------------
# Batched LLM Evaluation
# ---------------------------------------------------------------------------

async def evaluate_verdict_batch(
    batch: list[StepVerdictContext],
    provider_id: str,
) -> tuple[dict[str, LLMVerdictItem], str, int, float]:
    """Execute one batched LLM verifier-corrector call (<=5 steps)."""
    alias_of = {ctx.user_step_id: ctx.step_alias for ctx in batch}
    real_of = {ctx.step_alias: ctx.user_step_id for ctx in batch}
    expected_aliases = set(alias_of.values())

    prompt_payload = []
    for ctx in batch:
        # Build BD mappings with alias keys
        bd_mappings_payload = []
        for p in ctx.bd_proposals:
            alias = ctx.bd_id_to_alias.get(p.get("bd_id", ""), p.get("bd_id", ""))
            bd_mappings_payload.append({
                "bd_unit_id": alias,
                "bd_kind": p.get("bd_kind"),
                "name": p.get("name"),
                "functionality": p.get("functionality"),
                "relation": p.get("relation"),
                "confidence": p.get("confidence"),
                "bd_code_verdict": p.get("bd_code_verdict"),
                "reason": p.get("reason"),
            })

        prompt_payload.append({
            "unit_id": ctx.step_alias,
            "section_id": ctx.section_id,
            "kind": ctx.kind,
            "text_ja": ctx.text_ja,
            "text_en": ctx.text_en,
            "trigger_ja": ctx.trigger_ja,
            "expected_ja": ctx.expected_ja,
            "screen_name_ja": ctx.screen_name_ja,
            "presentation_note": "Tagged as presentation/visual claim (prior: likely UNVERIFIABLE unless code snippet demonstrates logic)" if ctx.presentation else None,
            "anchor_result": {
                "matched": len(ctx.anchor_proposals) > 0,
                "citations": [
                    {
                        "rel_path": a.get("rel_path"),
                        "line_start": a.get("line_start"),
                        "line_end": a.get("line_end"),
                        "fetched_text": a.get("fetched_text"),
                        "reason": a.get("reason"),
                    }
                    for a in ctx.anchor_proposals
                ],
            },
            "bd_mappings": bd_mappings_payload,
            "snippets": [
                {
                    "rel_path": s.rel_path,
                    "line_start": s.line_start,
                    "line_end": s.line_end,
                    "text": s.text,
                }
                for s in ctx.snippets
            ],
        })

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_VERDICT_SYSTEM_PROMPT),
                ChatMessage(role="user", content=json.dumps(prompt_payload, ensure_ascii=False, indent=2)),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> dict[str, LLMVerdictItem]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        validated = LLMVerdictBatchResponse.model_validate(parsed)
        assert_exact_id_coverage([r.unit_id for r in validated.results], expected_aliases, "User flow verdict verifier")
        return {r.unit_id: r for r in validated.results}

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="User flow verdict verifier (LLM#4)", provider_id=provider_id
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retries = max(attempts["n"] - 1, 0)

    if ladder_res is None:
        return {}, "", retries, latency_ms

    parsed_items, raw_resp = ladder_res
    results: dict[str, LLMVerdictItem] = {}
    for alias_id, item in parsed_items.items():
        real_uid = real_of.get(alias_id, alias_id)
        # Remap kept_bd_ids back from alias (e.g. bd1 -> real bd_id)
        ctx_for_step = next((c for c in batch if c.user_step_id == real_uid), None)
        if ctx_for_step:
            remapped_kept = []
            for b_alias in item.kept_bd_ids:
                if b_alias in ctx_for_step.bd_alias_map:
                    remapped_kept.append(ctx_for_step.bd_alias_map[b_alias]["bd_id"])
                else:
                    # If model returned real bd_id directly
                    remapped_kept.append(b_alias)
            item.kept_bd_ids = remapped_kept
        results[real_uid] = item

    return results, raw_resp, retries, latency_ms


# ---------------------------------------------------------------------------
# Public Orchestration
# ---------------------------------------------------------------------------

async def run_user_flow_verdicts(
    db: Any,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
    run_id: str,
    provider_id: str | None = None,
    skipped_step_ids: set[str] | None = None,
    activities: list[dict[str, Any]] | None = None,
    step_bd_scope: dict[str, list[str]] | None = None,
) -> VerdictRunSummary:
    """Execute Phase U Stage 4: Verdict verification on user steps + activity rollups + flow rollups + BD_EXTRA.

    `step_bd_scope` (Phase U2 tier-2 rescope, optional): step_id -> bd_flow_ids matched by tier-1
    activity matching. When provided, the BD catalog loaded for the verifier is shrunk to the
    union of all evaluated steps' matched flows. None (the default) reproduces today's unscoped
    behavior byte-for-byte.
    """
    now = utc_now_iso()
    skipped_set = skipped_step_ids or set()

    # 1. Fetch snapshot root directory
    async with db.execute("SELECT local_path FROM repo_snapshots WHERE id = ?", (snapshot_id,)) as cur:
        snap_row = await cur.fetchone()
    local_path = snap_row[0] if isinstance(snap_row, (tuple, list)) else (snap_row["local_path"] if snap_row else "")
    local_p = Path(local_path) if local_path else Path(".")

    # 2. Fetch manifest files
    async with db.execute("SELECT rel_path FROM manifest_files WHERE snapshot_id = ?", (snapshot_id,)) as cur:
        manifest_rows = await cur.fetchall()
    manifest_paths = [r[0] if isinstance(r, (tuple, list)) else r["rel_path"] for r in manifest_rows]

    # 3. Load user flows & steps
    async with db.execute(
        "SELECT id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note FROM user_flows WHERE doc_id = ? ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        flow_rows = await cur.fetchall()
    flows = [dict(r) if hasattr(r, "keys") else {
        "id": r[0], "doc_id": r[1], "ordinal": r[2], "name_ja": r[3], "name_en": r[4], "kind": r[5], "sheet": r[6], "scope_note": r[7]
    } for r in flow_rows]

    async with db.execute(
        "SELECT s.id, s.flow_id, s.ordinal, s.kind, s.section_id, s.text_ja, s.text_en, "
        "       s.trigger_ja, s.expected_ja, s.screen_name_ja, s.in_scope, s.scope_note, "
        "       s.sheet, s.row_start, s.row_end "
        "FROM user_steps s JOIN user_flows f ON s.flow_id = f.id "
        "WHERE f.doc_id = ? ORDER BY s.ordinal",
        (doc_id,),
    ) as cur:
        step_rows = await cur.fetchall()
    steps = [dict(r) if hasattr(r, "keys") else {
        "id": r[0], "flow_id": r[1], "ordinal": r[2], "kind": r[3], "section_id": r[4], "text_ja": r[5], "text_en": r[6],
        "trigger_ja": r[7], "expected_ja": r[8], "screen_name_ja": r[9], "in_scope": r[10], "scope_note": r[11],
        "sheet": r[12], "row_start": r[13], "row_end": r[14]
    } for r in step_rows]

    # 4. Load persisted anchors & mappings & align audit artifacts
    async with db.execute(
        "SELECT step_id, rel_path, line_start, line_end, kind, valid, reason FROM user_code_anchors "
        "WHERE snapshot_id = ? AND step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?)",
        (snapshot_id, doc_id),
    ) as cur:
        anchor_rows = await cur.fetchall()
    anchors_by_step: dict[str, list[dict[str, Any]]] = {}
    for r in anchor_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "step_id": r[0], "rel_path": r[1], "line_start": r[2], "line_end": r[3], "kind": r[4], "valid": r[5], "reason": r[6]
        }
        anchors_by_step.setdefault(d["step_id"], []).append(d)

    async with db.execute(
        "SELECT user_step_id, bd_kind, bd_id, relation, confidence, reason FROM user_bd_mappings "
        "WHERE user_step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?)",
        (doc_id,),
    ) as cur:
        mapping_rows = await cur.fetchall()
    mappings_by_step: dict[str, list[dict[str, Any]]] = {}
    for r in mapping_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "user_step_id": r[0], "bd_kind": r[1], "bd_id": r[2], "relation": r[3], "confidence": r[4], "reason": r[5]
        }
        mappings_by_step.setdefault(d["user_step_id"], []).append(d)

    async with db.execute(
        "SELECT ref_id, payload FROM user_run_artifacts WHERE doc_id = ? AND ref_id LIKE 'align:%'",
        (doc_id,),
    ) as cur:
        align_art_rows = await cur.fetchall()
    align_artifacts_by_step: dict[str, dict[str, Any]] = {}
    for r in align_art_rows:
        ref_id = r[0] if isinstance(r, (tuple, list)) else r["ref_id"]
        payload_str = r[1] if isinstance(r, (tuple, list)) else r["payload"]
        step_id = ref_id.split(":", 1)[1] if ":" in ref_id else ref_id
        try:
            align_artifacts_by_step[step_id] = json.loads(payload_str)
        except Exception:
            align_artifacts_by_step[step_id] = {}

    # 5. Separate in-scope vs out-of-scope steps (Deterministic Pre-pass)
    in_scope_steps: list[dict[str, Any]] = []
    eval_steps: list[dict[str, Any]] = []
    tier1_skipped_steps: list[dict[str, Any]] = []
    out_of_scope_steps: list[dict[str, Any]] = []
    for s in steps:
        if s.get("in_scope", 1) == 1:
            in_scope_steps.append(s)
            if s["id"] in skipped_set:
                tier1_skipped_steps.append(s)
            else:
                eval_steps.append(s)
        else:
            out_of_scope_steps.append(s)

    # 6. Load BD context + BD<->code unit verdicts (union-scoped across eval_steps' matched
    # flows when step_bd_scope is given; identical unscoped load otherwise)
    if step_bd_scope:
        union_flow_ids: set[str] = set()
        for s in eval_steps:
            union_flow_ids.update(step_bd_scope.get(s["id"]) or [])
        bd_ctx = await load_bd_context(db, cluster_id, bd_flow_ids=sorted(union_flow_ids) or None)
    else:
        bd_ctx = await load_bd_context(db, cluster_id)
    bd_unit_by_id = {u.unit_id: u for u in bd_ctx.units}

    async with db.execute(
        "SELECT unit_id, verdict, reason, evidence_json FROM business_unit_verdicts WHERE cluster_id = ? AND snapshot_id = ?",
        (cluster_id, snapshot_id),
    ) as cur:
        bu_verdict_rows = await cur.fetchall()
    bd_verdicts_by_unit: dict[str, dict[str, Any]] = {}
    for r in bu_verdict_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "unit_id": r[0], "verdict": r[1], "reason": r[2], "evidence_json": r[3]
        }
        bd_verdicts_by_unit[d["unit_id"]] = d

    # 7. Build seed hits for retrieval
    manifest_paths_set = set(manifest_paths)
    hits_by_step = await seed_literal_hits(db, snapshot_id, eval_steps, local_p)
    hits_by_flow: dict[str, list[SeedHit]] = {}
    for s in eval_steps:
        f_id = s.get("flow_id", "")
        hits_by_flow.setdefault(f_id, []).extend(hits_by_step.get(s["id"], []))

    # 8. Assemble StepVerdictContext for active evaluated steps
    contexts: list[StepVerdictContext] = []
    for idx, s in enumerate(eval_steps, start=1):
        s_alias = f"u{idx}"
        s_hits = hits_by_step.get(s["id"], [])
        flow_id = s.get("flow_id", "")
        flow_hits = hits_by_flow.get(flow_id, [])

        screen_files = resolve_step_files(s, s_hits, manifest_paths_set, flow_hits)
        snippets = await retrieve_step_snippets(db, snapshot_id, s, s_hits, screen_files, local_p)
        snippet_id_map = {f"src{s_i}": snip for s_i, snip in enumerate(snippets, start=1)}

        # Load anchor proposals with fetched text
        raw_anchors = anchors_by_step.get(s["id"], [])
        anchor_proposals = []
        for a in raw_anchors:
            if a.get("valid", 0) == 1:
                rc = await resolve_citation(db, snapshot_id, a["rel_path"], a["line_start"], a["line_end"])
                anchor_proposals.append({
                    "rel_path": a["rel_path"],
                    "line_start": a["line_start"],
                    "line_end": a["line_end"],
                    "fetched_text": rc.fetched_text,
                    "reason": a.get("reason"),
                })

        # Load BD proposals enriched with BD unit metadata & BD<->code verdict
        raw_mappings = mappings_by_step.get(s["id"], [])
        bd_proposals = []
        bd_alias_map = {}
        bd_id_to_alias = {}
        for b_i, m in enumerate(raw_mappings, start=1):
            bd_id = m["bd_id"]
            bd_alias = f"bd{b_i}"
            bd_unit = bd_unit_by_id.get(bd_id)
            bu_v = bd_verdicts_by_unit.get(bd_id, {})

            p_data = {
                "bd_id": bd_id,
                "bd_alias": bd_alias,
                "bd_kind": m.get("bd_kind") or (bd_unit.bd_kind if bd_unit else "step"),
                "name": bd_unit.name if bd_unit else bd_id,
                "functionality": bd_unit.description if bd_unit else "",
                "relation": m.get("relation", "realizes"),
                "confidence": m.get("confidence", 0.8),
                "bd_code_verdict": bu_v.get("verdict", "UNKNOWN"),
                "reason": m.get("reason", ""),
            }
            bd_proposals.append(p_data)
            bd_alias_map[bd_alias] = p_data
            bd_id_to_alias[bd_id] = bd_alias

        # Presentation flag from align artifact
        align_art = align_artifacts_by_step.get(s["id"], {})
        is_presentation = bool(align_art.get("presentation", False))

        contexts.append(
            StepVerdictContext(
                user_step_id=s["id"],
                step_alias=s_alias,
                flow_id=flow_id,
                ordinal=s.get("ordinal", idx),
                kind=s.get("kind", "action"),
                section_id=s.get("section_id"),
                text_ja=s.get("text_ja", ""),
                text_en=s.get("text_en"),
                trigger_ja=s.get("trigger_ja"),
                expected_ja=s.get("expected_ja"),
                screen_name_ja=s.get("screen_name_ja"),
                in_scope=True,
                scope_note=s.get("scope_note"),
                presentation=is_presentation,
                snippets=snippets,
                snippet_id_map=snippet_id_map,
                anchor_proposals=anchor_proposals,
                bd_proposals=bd_proposals,
                bd_alias_map=bd_alias_map,
                bd_id_to_alias=bd_id_to_alias,
            )
        )

    # 9. LLM Execution in batches (<=5 steps)
    batches = [contexts[i:i + _VERDICT_BATCH_SIZE] for i in range(0, len(contexts), _VERDICT_BATCH_SIZE)]
    llm_results_by_step: dict[str, LLMVerdictItem] = {}
    step_meta_by_step: dict[str, dict[str, Any]] = {}
    llm_batches_issued = 0
    total_physical_attempts = 0

    if provider_id and batches:
        semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

        async def _run_verdict_batch(b: list[StepVerdictContext]) -> dict[str, Any]:
            async with semaphore:
                b_results, raw_resp, retries, lat = await evaluate_verdict_batch(b, provider_id)
                return {
                    "results": b_results,
                    "raw_resp": raw_resp,
                    "retries": retries,
                    "latency_ms": lat,
                }

        batch_outputs = await asyncio.gather(*[_run_verdict_batch(b) for b in batches], return_exceptions=True)
        for b, out in zip(batches, batch_outputs):
            llm_batches_issued += 1
            if isinstance(out, Exception):
                logger.error(f"Verdict batch failed: {out!r}")
                continue
            if isinstance(out, dict):
                total_physical_attempts += (1 + out.get("retries", 0))
                llm_results_by_step.update(out["results"])
                for ctx in b:
                    step_meta_by_step[ctx.user_step_id] = {
                        "raw_resp": out["raw_resp"],
                        "retries": out["retries"],
                        "latency_ms": out["latency_ms"],
                    }

    # 10. Deterministic Fusion & Gating per step
    step_verdicts_to_insert: list[tuple[str, str, str, str, str, str, str, str, str, str | None, str, str, str]] = []
    verdict_artifacts_to_insert: list[tuple[str, str, str, str, str, str]] = []

    final_step_verdicts: dict[str, str] = {}
    surviving_kept_bd_ids: set[str] = set()

    # Process in-scope steps
    for ctx in contexts:
        sid = ctx.user_step_id
        model_out = llm_results_by_step.get(sid)
        meta = step_meta_by_step.get(sid, {})

        if model_out is None:
            # Fallback when no provider or LLM failed
            if provider_id is None:
                # Offline heuristic: if valid code anchors exist and mapped BD units exist -> COVERED
                if ctx.anchor_proposals and ctx.bd_proposals:
                    v_verdict = "COVERED"
                    v_div = None
                    v_kept = [p["bd_id"] for p in ctx.bd_proposals]
                    v_reason = "Offline run: step has code anchors and BD mappings."
                    v_basis = "own_citation"
                    v_corrected = False
                    cits_resolved = [
                        ResolvedCitation(
                            valid=True,
                            rel_path=a["rel_path"],
                            line_start=a["line_start"],
                            line_end=a["line_end"],
                            fetched_text=a.get("fetched_text"),
                            source_sha256=None,
                            reject_reason=None,
                        )
                        for a in ctx.anchor_proposals
                    ]
                elif ctx.anchor_proposals:
                    v_verdict = "BD_MISSING"
                    v_div = "behavioural"
                    v_kept = []
                    v_reason = "Offline run: step has code anchors but no BD mappings."
                    v_basis = "own_citation"
                    v_corrected = False
                    cits_resolved = [
                        ResolvedCitation(
                            valid=True,
                            rel_path=a["rel_path"],
                            line_start=a["line_start"],
                            line_end=a["line_end"],
                            fetched_text=a.get("fetched_text"),
                            source_sha256=None,
                            reject_reason=None,
                        )
                        for a in ctx.anchor_proposals
                    ]
                else:
                    v_verdict = "UNVERIFIABLE"
                    v_div = None
                    v_kept = []
                    v_reason = "Offline run (no provider): insufficient evidence."
                    v_basis = None
                    v_corrected = False
                    cits_resolved = []
            else:
                v_verdict = "UNVERIFIABLE"
                v_div = None
                v_kept = []
                v_reason = "LLM verifier did not return a valid response (LLM_NO_RESPONSE)."
                v_basis = None
                v_corrected = False
                cits_resolved = []
        else:
            v_verdict = model_out.verdict.upper()
            v_div = model_out.divergence
            v_kept = list(model_out.kept_bd_ids)
            v_reason = model_out.reason
            v_corrected = model_out.corrected
            v_basis = None

            # Resolve verifier citations + window containment check
            cits_resolved = []
            for c in model_out.citations:
                rc = await resolve_citation(db, snapshot_id, c.rel_path, c.line_start, c.line_end)
                # Verify containment against snippets shown to verifier
                if rc.valid and not any(
                    s.rel_path == rc.rel_path and rc.line_start >= s.line_start and rc.line_end <= s.line_end
                    for s in ctx.snippets
                ):
                    rc = ResolvedCitation(
                        valid=False,
                        rel_path=rc.rel_path,
                        line_start=rc.line_start,
                        line_end=rc.line_end,
                        fetched_text=rc.fetched_text,
                        source_sha256=rc.source_sha256,
                        reject_reason="CITATION_OUT_OF_WINDOW",
                    )
                cits_resolved.append(rc)

            any_invalid = any(not rc.valid for rc in cits_resolved)
            valid_cits = [rc for rc in cits_resolved if rc.valid]

            # Fusion Rule 1: ANY invalid citation forces UNVERIFIABLE
            if any_invalid:
                v_verdict = "UNVERIFIABLE"
                v_div = None
                if any(rc.reject_reason == "CITATION_OUT_OF_WINDOW" for rc in cits_resolved):
                    v_reason = f"{v_reason} [CITATION_OUT_OF_WINDOW]"
                else:
                    v_reason = f"{v_reason} [CITATION_INVALID]"
            # Fusion Rule 2: BD_MISSING requires >= 1 valid citation
            elif v_verdict == "BD_MISSING":
                if not valid_cits:
                    v_verdict = "UNVERIFIABLE"
                    v_div = None
                    v_reason = f"{v_reason} [NO_CITATION]"
                else:
                    v_basis = "own_citation"
            # Fusion Rule 3: CONTRADICTED requires >= 1 valid citation. A divergence finding reported
            # to the customer must cite the code line showing the actual behavior — keeping a (merely
            # relevant) BD mapping does NOT prove a conflict, so it cannot substitute for evidence.
            elif v_verdict == "CONTRADICTED":
                if not valid_cits:
                    v_verdict = "UNVERIFIABLE"
                    v_div = None
                    v_reason = f"{v_reason} [NO_CITATION]"
                else:
                    v_basis = "own_citation"
            # Fusion Rule 4: COVERED requires valid citation OR mapped BD unit with MATCH/PARTIAL
            elif v_verdict == "COVERED":
                if valid_cits:
                    v_basis = "own_citation"
                else:
                    # Check if any kept BD mapping has BD<->code verdict MATCH or PARTIAL
                    matching_bd = any(
                        bd_verdicts_by_unit.get(b_id, {}).get("verdict") in ("MATCH", "PARTIAL")
                        for b_id in v_kept
                    )
                    if matching_bd:
                        v_basis = "bd_verdict"
                    else:
                        v_verdict = "UNVERIFIABLE"
                        v_div = None
                        v_reason = f"{v_reason} [NO_CITATION]"

        # Record surviving kept BD IDs
        if v_verdict == "COVERED":
            surviving_kept_bd_ids.update(v_kept)

        final_step_verdicts[sid] = v_verdict

        ev_json = json.dumps({
            "kept_bd_ids": v_kept,
            "basis": v_basis,
            "corrected": v_corrected,
            "citations": [
                {
                    "rel_path": rc.rel_path,
                    "line_start": rc.line_start,
                    "line_end": rc.line_end,
                    "valid": rc.valid,
                    "fetched_text": rc.fetched_text,
                    "reject_reason": rc.reject_reason,
                }
                for rc in cits_resolved
            ],
            "upstream_anchors": ctx.anchor_proposals,
            "upstream_bd_mappings": ctx.bd_proposals,
        }, ensure_ascii=False)

        verdict_id = f"uv:{new_id()}"
        step_verdicts_to_insert.append((
            verdict_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "user",
            sid,
            "step",
            v_verdict,
            v_div,
            v_reason,
            ev_json,
            now,
        ))

        # Append step verdict audit artifact
        verdict_artifacts_to_insert.append((
            f"ura:{new_id()}",
            run_id,
            doc_id,
            f"verdict:{sid}",
            json.dumps({
                "user_step_id": sid,
                "step_alias": ctx.step_alias,
                "verdict": v_verdict,
                "divergence": v_div,
                "reason": v_reason,
                "corrected": v_corrected,
                "basis": v_basis,
                "kept_bd_ids": v_kept,
                "citation_resolutions": [
                    {
                        "rel_path": rc.rel_path,
                        "line_start": rc.line_start,
                        "line_end": rc.line_end,
                        "valid": rc.valid,
                        "fetched_text": rc.fetched_text,
                        "reject_reason": rc.reject_reason,
                    }
                    for rc in cits_resolved
                ],
                "raw_response": meta.get("raw_resp"),
                "retry_count": meta.get("retries", 0),
                "latency_ms": meta.get("latency_ms", 0.0),
            }, ensure_ascii=False),
            now,
        ))

    # Process Tier-1 skipped steps (NONE matched activity -> UNVERIFIABLE)
    for s in tier1_skipped_steps:
        sid = s["id"]
        v_verdict = "UNVERIFIABLE"
        v_div = None
        v_reason = "TIER1_NONE_MATCH: Step skipped Tier-2 alignment because its activity has no matching BD flow."
        final_step_verdicts[sid] = v_verdict

        ev_json = json.dumps({
            "kept_bd_ids": [],
            "basis": "tier1_none_match",
            "corrected": False,
            "citations": [],
            "skipped": True,
        }, ensure_ascii=False)

        verdict_id = f"uv:{new_id()}"
        step_verdicts_to_insert.append((
            verdict_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "user",
            sid,
            "step",
            v_verdict,
            v_div,
            v_reason,
            ev_json,
            now,
        ))

    # Process out-of-scope steps (Pre-pass output)
    for s in out_of_scope_steps:
        sid = s["id"]
        v_verdict = "OUT_OF_SCOPE"
        v_div = "scope"
        v_reason = s.get("scope_note") or "Step marked out of scope"
        final_step_verdicts[sid] = v_verdict

        ev_json = json.dumps({
            "kept_bd_ids": [],
            "basis": None,
            "corrected": False,
            "citations": [],
            "scope_note": s.get("scope_note"),
        }, ensure_ascii=False)

        verdict_id = f"uv:{new_id()}"
        step_verdicts_to_insert.append((
            verdict_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "user",
            sid,
            "step",
            v_verdict,
            v_div,
            v_reason,
            ev_json,
            now,
        ))

    # 11. Deterministic Flow-Level Rollup (§3b)
    flow_verdicts_to_insert: list[tuple[str, str, str, str, str, str, str, str, str, str | None, str, str, str]] = []
    final_flow_verdicts: dict[str, str] = {}

    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    for s in steps:
        steps_by_flow.setdefault(s["flow_id"], []).append(s)

    for f in flows:
        f_id = f["id"]
        f_steps = steps_by_flow.get(f_id, [])
        f_in_scope = [s for s in f_steps if s.get("in_scope", 1) == 1]

        counts = {
            "COVERED": 0,
            "BD_MISSING": 0,
            "CONTRADICTED": 0,
            "UNVERIFIABLE": 0,
            "OUT_OF_SCOPE": 0,
        }
        for s in f_steps:
            s_verdict = final_step_verdicts.get(s["id"], "UNVERIFIABLE")
            counts[s_verdict] = counts.get(s_verdict, 0) + 1

        if len(f_steps) == 0 or counts["OUT_OF_SCOPE"] == len(f_steps):
            flow_match = "OUT_OF_SCOPE"
            flow_div = "scope"
        elif counts["CONTRADICTED"] > 0:
            flow_match = "DIVERGENT"
            flow_div = "behavioural"
        elif len(f_in_scope) > 0 and counts["COVERED"] == len(f_in_scope):
            flow_match = "MATCHED"
            flow_div = None
        elif counts["COVERED"] > 0:
            flow_match = "PARTIAL"
            flow_div = None
        else:
            flow_match = "UNCOVERED"
            flow_div = None

        final_flow_verdicts[f_id] = flow_match
        flow_reason = (
            f"Flow rollup {flow_match}: {counts['COVERED']} covered, "
            f"{counts['BD_MISSING']} missing, {counts['CONTRADICTED']} contradicted, "
            f"{counts['UNVERIFIABLE']} unverifiable, {counts['OUT_OF_SCOPE']} out_of_scope"
        )
        flow_ev_json = json.dumps({
            "flow_match": flow_match,
            "counts": counts,
            "total_steps": len(f_steps),
            "in_scope_steps": len(f_in_scope),
        }, ensure_ascii=False)

        flow_verdict_id = f"uv:{new_id()}"
        flow_verdicts_to_insert.append((
            flow_verdict_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "user",
            f_id,
            "flow",
            flow_match,
            flow_div,
            flow_reason,
            flow_ev_json,
            now,
        ))

    # 11b. Deterministic Activity-Level Rollup (Phase U2)
    activity_verdicts_to_insert: list[tuple[str, str, str, str, str, str, str, str, str, str | None, str, str, str]] = []
    final_activity_verdicts: dict[str, str] = {}

    act_list = activities
    if act_list is None:
        async with db.execute(
            "SELECT id, flow_id, ordinal, name_en, name_ja, member_step_ids_json FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?) ORDER BY ordinal",
            (doc_id,),
        ) as cur:
            act_rows = await cur.fetchall()
        act_list = [
            dict(r) if hasattr(r, "keys") else {
                "id": r[0], "flow_id": r[1], "ordinal": r[2], "name_en": r[3], "name_ja": r[4], "member_step_ids_json": r[5]
            }
            for r in act_rows
        ]

    for act in act_list:
        act_id = act["id"]
        try:
            member_ids = json.loads(act.get("member_step_ids_json") or "[]")
        except Exception:
            member_ids = []
        act_steps = [s for s in steps if s["id"] in member_ids]
        act_in_scope = [s for s in act_steps if s.get("in_scope", 1) == 1]

        counts = {
            "COVERED": 0,
            "BD_MISSING": 0,
            "CONTRADICTED": 0,
            "UNVERIFIABLE": 0,
            "OUT_OF_SCOPE": 0,
        }
        for s in act_steps:
            s_verdict = final_step_verdicts.get(s["id"], "UNVERIFIABLE")
            counts[s_verdict] = counts.get(s_verdict, 0) + 1

        if len(act_steps) == 0 or counts["OUT_OF_SCOPE"] == len(act_steps):
            act_match = "OUT_OF_SCOPE"
            act_div = "scope"
        elif counts["CONTRADICTED"] > 0:
            act_match = "DIVERGENT"
            act_div = "behavioural"
        elif len(act_in_scope) > 0 and counts["COVERED"] == len(act_in_scope):
            act_match = "MATCHED"
            act_div = None
        elif counts["COVERED"] > 0:
            act_match = "PARTIAL"
            act_div = None
        else:
            act_match = "UNCOVERED"
            act_div = None

        final_activity_verdicts[act_id] = act_match
        act_reason = (
            f"Activity rollup {act_match}: {counts['COVERED']} covered, "
            f"{counts['BD_MISSING']} missing, {counts['CONTRADICTED']} contradicted, "
            f"{counts['UNVERIFIABLE']} unverifiable, {counts['OUT_OF_SCOPE']} out_of_scope"
        )
        act_ev_json = json.dumps({
            "activity_match": act_match,
            "counts": counts,
            "total_steps": len(act_steps),
            "in_scope_steps": len(act_in_scope),
        }, ensure_ascii=False)

        act_verdict_id = f"uv:{new_id()}"
        activity_verdicts_to_insert.append((
            act_verdict_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "user",
            act_id,
            "activity",
            act_match,
            act_div,
            act_reason,
            act_ev_json,
            now,
        ))

    # 12. BD-Side Verdicts (BD_EXTRA vs COVERED)
    bd_verdicts_to_insert: list[tuple[str, str, str, str, str, str, str, str, str, str | None, str, str, str]] = []
    all_mapped_bd_ids: set[str] = set()
    for m_list in mappings_by_step.values():
        for m in m_list:
            all_mapped_bd_ids.add(m["bd_id"])

    # BD-side must enumerate the FULL cluster catalog: with step_bd_scope, bd_ctx is union-scoped
    # to tier-1-matched flows only, which would silently drop unmatched flows' units — precisely
    # the strongest BD_EXTRA candidates ("BD describes it, no customer flow references it").
    bd_side_ctx = bd_ctx
    if step_bd_scope:
        bd_side_ctx = await load_bd_context(db, cluster_id)

    bd_extra_count = 0
    for u in bd_side_ctx.units:
        is_referenced = (u.unit_id in surviving_kept_bd_ids) or (u.unit_id in all_mapped_bd_ids)
        if is_referenced:
            bd_side_verdict = "COVERED"
            bd_side_div = None
            bd_side_reason = "Referenced by user flow mapping."
        else:
            bd_side_verdict = "BD_EXTRA"
            bd_side_div = "scope"
            bd_side_reason = "BD describes behavior not referenced by any user flow step."
            bd_extra_count += 1

        bd_v_id = f"uv:{new_id()}"
        bd_verdicts_to_insert.append((
            bd_v_id,
            run_id,
            doc_id,
            cluster_id,
            snapshot_id,
            "bd",
            u.unit_id,
            u.bd_kind,
            bd_side_verdict,
            bd_side_div,
            bd_side_reason,
            json.dumps({"name": u.name, "description": u.description}, ensure_ascii=False),
            now,
        ))

    # 13. Delete + Replace user_verdicts for (doc_id, cluster_id, snapshot_id)
    await db.execute(
        "DELETE FROM user_verdicts WHERE doc_id = ? AND cluster_id = ? AND snapshot_id = ?",
        (doc_id, cluster_id, snapshot_id),
    )

    # Insert step, activity, flow, and bd verdicts
    all_verdicts = step_verdicts_to_insert + activity_verdicts_to_insert + flow_verdicts_to_insert + bd_verdicts_to_insert
    for v in all_verdicts:
        await db.execute(
            "INSERT INTO user_verdicts (id, run_id, doc_id, cluster_id, snapshot_id, side, ref_id, ref_kind, verdict, divergence, reason, evidence_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            v,
        )

    # Insert run artifacts
    for art in verdict_artifacts_to_insert:
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            art,
        )

    await db.commit()

    # 14. Compute global summary counts
    global_counts = {
        "COVERED": 0,
        "BD_MISSING": 0,
        "CONTRADICTED": 0,
        "UNVERIFIABLE": 0,
        "OUT_OF_SCOPE": 0,
    }
    for v in final_step_verdicts.values():
        global_counts[v] = global_counts.get(v, 0) + 1

    return VerdictRunSummary(
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
        total_steps=len(steps),
        in_scope_steps=len(in_scope_steps),
        covered_steps=global_counts["COVERED"],
        bd_missing_steps=global_counts["BD_MISSING"],
        contradicted_steps=global_counts["CONTRADICTED"],
        unverifiable_steps=global_counts["UNVERIFIABLE"],
        out_of_scope_steps=global_counts["OUT_OF_SCOPE"],
        bd_extra_count=bd_extra_count,
        flow_verdicts=final_flow_verdicts,
        step_verdicts=final_step_verdicts,
        activity_verdicts=final_activity_verdicts,
        llm_batches_issued=llm_batches_issued,
        physical_attempts=total_physical_attempts,
    )
