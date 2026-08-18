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
   - COVERED requires >=1 verifier-kept BD mapping AND (valid citation -> basis 'own_citation'
     | kept basis unit with BD<->code verdict MATCH/PARTIAL -> basis 'bd_verdict').
   - LLM_NO_RESPONSE -> UNVERIFIABLE.
4. BD-side verdicts (three-state, from verifier-KEPT mappings only):
   - COVERED: >= 1 kept mapping reached the unit.
   - BD_UNMAPPED: parent flow matched in tier-1 but no kept mapping reached the unit (recall artifact).
   - BD_EXTRA: parent flow has no accepted tier-1 pair (a real spec finding).
   - UNRESOLVED: tier-1 did not resolve, so the unit's status is unassessed (neither list).
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

from ._align import COVERAGE_RELATIONS, _shortlist_bd_candidates, render_bd_candidate_name
from ._anchor import (
    SeedHit,
    resolve_step_files,
    retrieve_step_snippets,
    seed_literal_hits,
)
from ._mapping import BDContext, BDUnit, load_bd_context


_VERDICT_BATCH_SIZE = 5
_MAX_EVIDENCE_SUMMARY = 3
_MAX_EVIDENCE_CHARS = 160


def _summarize_unit_evidence(evidence_json: str | None) -> list[str]:
    """Condense a BD unit's stored code evidence into a few short lines for the verifier payload.

    Basis (ii) asks the model whether a MATCH/PARTIAL unit really implements THIS step; without
    the evidence behind that stored verdict the judgment is blind, and for PARTIAL units it is the
    only way to tell which subclaim was actually verified."""
    if not evidence_json:
        return []
    try:
        items = json.loads(evidence_json)
    except Exception:
        return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []

    out: list[str] = []
    for it in items[:_MAX_EVIDENCE_SUMMARY]:
        if isinstance(it, dict):
            where = it.get("rel_path") or it.get("file") or ""
            line_s = it.get("line_start")
            line_e = it.get("line_end")
            text = (it.get("text") or it.get("snippet") or it.get("reason") or "").strip().replace("\n", " ")
            loc = f"{where}:{line_s}-{line_e}" if where and line_s is not None else where
            line = f"{loc} {text}".strip() if loc else text
        else:
            line = str(it).strip().replace("\n", " ")
        if line:
            out.append(line[:_MAX_EVIDENCE_CHARS])
    return out

_VERDICT_SYSTEM_PROMPT = """You are the FINAL verifier and corrector for user-flow alignment between customer business flows, \
AI-generated Business Design (BD) documents, and legacy mainframe source code.

For each user step, you are given:
- Step details: text_ja, text_en, kind, trigger, expected, section_id, screen_name_ja
- anchor_result: Upstream code anchor proposal (citations, fetched verbatim text, reason) — unverified hypothesis. You may ADOPT valid upstream anchor citations.
- bd_mappings: Upstream BD mapping proposals (mapped BD units, functionality, BD<->code verdict, BD<->code verdict reason, confidence, reason) — unverified hypothesis
- bd_candidates: The scoped candidate BD units for this step
- snippets: The real candidate source code snippets shown for this step

Treat upstream proposals as UNVERIFIED HYPOTHESES. Upstream agents may be wrong:
- The anchor matcher may have mis-cited or missed a contradicting line in the snippets.
- The BD mapper may have mapped to an irrelevant BD unit or missed a relevant one.
Re-judge strictly from the evidence shown. If an upstream output is wrong, CORRECT it:
set `corrected: true`, output your OWN kept_bd_ids (must be valid aliases from bd_candidates / bd_mappings), your OWN citations, and explain in `reason`.

Exact Verdict per user step (EXACTLY one of):
- COVERED: The step's business behavior is confirmed. Two admissible bases:
  (i) `own_citation`: >=1 valid citation in the shown snippets supports the step's outcome AND >=1 kept BD unit genuinely describes the behavior. (You may adopt valid upstream anchor citations).
  (ii) `bd_verdict`: >=1 kept BD unit has a BD<->code verdict of MATCH or PARTIAL that genuinely implements this step's behavior. In this case you MUST set `basis_bd_id` to that BD unit's alias (and it must also appear in `kept_bd_ids`); no code citation is required. Without a citation, a COVERED with no `basis_bd_id` is rejected.
      For a BD unit whose verdict is PARTIAL, its `bd_code_verdict_reason` / `bd_code_verdict_evidence` must support THIS step's specific subclaim — a PARTIAL unit verified on an unrelated part of its behavior is NOT an admissible basis.
  A kept BD unit whose upstream mapping `relation` is "related" is NOT coverage-bearing: it marks the same screen/area, not the same behavior. Keep it only for CONTRADICTED reasoning.
- BD_MISSING: Code evidence supports the user step (valid citation required) but NO candidate BD unit in the catalog describes it (e.g. customer feature absent from specification). If a valid citation supports the step and NO candidate BD unit fits, BD_MISSING is the expected answer (NOT UNVERIFIABLE) — but you must SAY it: emit verdict "BD_MISSING" with empty `kept_bd_ids`. A COVERED with empty `kept_bd_ids` is malformed and is discarded, never read as BD_MISSING.
- CONTRADICTED: A BD unit and user step assert DIFFERENT outcomes for the same behavior (e.g. BD says 'retry' vs user says 'show error'), OR code (cited) contradicts the user claim. Requires a positive conflict. Name the conflicting BD unit and/or cite the conflicting line.
- UNVERIFIABLE: Cannot confirm nor refute from available evidence (includes presentation claims: layout/color/font; includes cases where snippets are missing or insufficient). Prefer over guessing.

Divergence field:
- "scope": when the gap is explained by explicit scope exclusions (e.g. step text or notes say 'PoC: option 1 only' or 'out of scope').
- "behavioural": for BD_MISSING or CONTRADICTED when not explained by scope.
- null: for COVERED or UNVERIFIABLE.

Citations:
- Citations are LOCATIONS ONLY (rel_path, line_start, line_end) pointing inside the provided snippets. The system re-fetches the verbatim text.
- kept_bd_ids: list of BD unit aliases (e.g. ["bd1"]) from the candidate catalog / proposals that genuinely describe this step. Must use aliases from the provided registry.
- basis_bd_id: the BD unit alias (e.g. "bd1") providing the BD<->code verdict justification when using basis (ii), or null.

Output schema:
Respond ONLY with a JSON object:
{
  "results": [
    {
      "unit_id": "u1",
      "verdict": "COVERED",
      "divergence": null,
      "kept_bd_ids": ["bd1"],
      "basis_bd_id": "bd1",
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
    basis_bd_id: str | None = None
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
    bd_candidates: list[dict[str, Any]]
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
    bd_unmapped_count: int
    bd_unassessed_count: int
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
                "bd_code_verdict_reason": p.get("bd_code_verdict_reason", ""),
                "bd_code_verdict_evidence": p.get("bd_code_verdict_evidence", []),
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
            "bd_candidates": ctx.bd_candidates,
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

        # Closed schema validation: validate that kept_bd_ids and basis_bd_id use recognized aliases
        ctx_by_alias = {c.step_alias: c for c in batch}
        for r in validated.results:
            c = ctx_by_alias.get(r.unit_id)
            if c:
                for k_id in r.kept_bd_ids:
                    if k_id not in c.bd_alias_map and k_id not in c.bd_id_to_alias:
                        raise ValueError(f"User flow verdict verifier: unknown kept_bd_id '{k_id}' for step {r.unit_id}")
                if r.basis_bd_id:
                    if r.basis_bd_id not in c.bd_alias_map and r.basis_bd_id not in c.bd_id_to_alias:
                        raise ValueError(f"User flow verdict verifier: unknown basis_bd_id '{r.basis_bd_id}' for step {r.unit_id}")

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
        # Remap kept_bd_ids and basis_bd_id back from alias (e.g. bd1 -> real bd_id)
        ctx_for_step = next((c for c in batch if c.user_step_id == real_uid), None)
        if ctx_for_step:
            remapped_kept = []
            for b_alias in item.kept_bd_ids:
                if b_alias in ctx_for_step.bd_alias_map:
                    remapped_kept.append(ctx_for_step.bd_alias_map[b_alias]["bd_id"])
                elif b_alias in ctx_for_step.bd_id_to_alias:
                    remapped_kept.append(b_alias)
            item.kept_bd_ids = remapped_kept
            if item.basis_bd_id:
                if item.basis_bd_id in ctx_for_step.bd_alias_map:
                    item.basis_bd_id = ctx_for_step.bd_alias_map[item.basis_bd_id]["bd_id"]
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
    unresolved_step_ids: set[str] | None = None,
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
    local_p = Path(local_path) if local_path and str(local_path).strip() else ""

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

    # 4. Load persisted anchors & mappings & align audit artifacts for the current run
    async with db.execute(
        "SELECT step_id, rel_path, line_start, line_end, kind, valid, reason FROM user_code_anchors "
        "WHERE snapshot_id = ? AND run_id = ? AND step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?)",
        (snapshot_id, run_id, doc_id),
    ) as cur:
        anchor_rows = await cur.fetchall()
    anchors_by_step: dict[str, list[dict[str, Any]]] = {}
    for r in anchor_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "step_id": r[0], "rel_path": r[1], "line_start": r[2], "line_end": r[3], "kind": r[4], "valid": r[5], "reason": r[6]
        }
        anchors_by_step.setdefault(d["step_id"], []).append(d)

    # This stage owns the 'ubmv:' rows it synthesizes for verifier-added mappings; drop the prior
    # judgment's rows first so a re-judged run starts from the mapper's own proposals only.
    await db.execute("DELETE FROM user_bd_mappings WHERE run_id = ? AND id LIKE 'ubmv:%'", (run_id,))

    async with db.execute(
        "SELECT user_step_id, bd_kind, bd_id, relation, confidence, reason FROM user_bd_mappings "
        "WHERE run_id = ? AND user_step_id IN (SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?)",
        (run_id, doc_id),
    ) as cur:
        mapping_rows = await cur.fetchall()
    mappings_by_step: dict[str, list[dict[str, Any]]] = {}
    for r in mapping_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "user_step_id": r[0], "bd_kind": r[1], "bd_id": r[2], "relation": r[3], "confidence": r[4], "reason": r[5]
        }
        mappings_by_step.setdefault(d["user_step_id"], []).append(d)

    async with db.execute(
        "SELECT ref_id, payload FROM user_run_artifacts WHERE doc_id = ? AND run_id = ? AND ref_id LIKE 'align:%'",
        (doc_id, run_id),
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

        # Build the step's BD candidate registry. Preferred source: the EXACT registry the mapper
        # saw, replayed from its align artifact — rebuilding it here would score with different
        # inputs and could show the verifier a different catalog than the one the proposals came
        # from, which makes "no candidate fits" (BD_MISSING) unsafe. The rebuild is the fallback
        # for runs whose align stage predates the registry (or ran offline).
        align_art = align_artifacts_by_step.get(s["id"], {})
        persisted_registry = align_art.get("bd_registry") or []

        registry_entries: list[dict[str, Any]]
        if persisted_registry:
            registry_entries = [
                {
                    "bd_id": e.get("bd_id"),
                    "bd_kind": e.get("bd_kind", "step"),
                    "name": e.get("name") or e.get("bd_id"),
                    "flow_name": e.get("flow_name", ""),
                    "functionality": e.get("functionality", ""),
                }
                for e in persisted_registry
                if e.get("bd_id")
            ]
        else:
            step_code_files = set(screen_files)
            registry_entries = [
                {
                    "bd_id": u.unit_id,
                    "bd_kind": u.bd_kind,
                    "name": render_bd_candidate_name(u),
                    "flow_name": u.flow_name,
                    "functionality": u.description or "",
                }
                for u in _shortlist_bd_candidates(s, bd_ctx, step_code_files)
            ]

        bd_candidates = []
        bd_alias_map = {}
        bd_id_to_alias = {}

        for c_i, entry in enumerate(registry_entries, start=1):
            c_alias = f"bd{c_i}"
            bu_v = bd_verdicts_by_unit.get(entry["bd_id"], {})
            u_data = {
                **entry,
                "bd_alias": c_alias,
                "bd_code_verdict": bu_v.get("verdict", "UNKNOWN"),
                "bd_code_verdict_reason": bu_v.get("reason", ""),
                "bd_code_verdict_evidence": _summarize_unit_evidence(bu_v.get("evidence_json")),
            }
            bd_candidates.append(u_data)
            bd_alias_map[c_alias] = u_data
            bd_id_to_alias[entry["bd_id"]] = c_alias

        # Load BD proposals referencing the candidate aliases (or creating new alias if outside shortlist)
        raw_mappings = mappings_by_step.get(s["id"], [])
        bd_proposals = []
        for m in raw_mappings:
            bd_id = m["bd_id"]
            bd_alias = bd_id_to_alias.get(bd_id)
            if not bd_alias:
                bd_alias = f"bd{len(bd_candidates) + len(bd_proposals) + 1}"
                bd_unit = bd_unit_by_id.get(bd_id)
                bu_v = bd_verdicts_by_unit.get(bd_id, {})
                p_data = {
                    "bd_id": bd_id,
                    "bd_alias": bd_alias,
                    "bd_kind": m.get("bd_kind") or (bd_unit.bd_kind if bd_unit else "step"),
                    "name": render_bd_candidate_name(bd_unit) if bd_unit else bd_id,
                    "flow_name": bd_unit.flow_name if bd_unit else "",
                    "functionality": bd_unit.description if bd_unit else "",
                    "relation": m.get("relation", "realizes"),
                    "confidence": m.get("confidence"),
                    "bd_code_verdict": bu_v.get("verdict", "UNKNOWN"),
                    "bd_code_verdict_reason": bu_v.get("reason", ""),
                    "bd_code_verdict_evidence": _summarize_unit_evidence(bu_v.get("evidence_json")),
                    "reason": m.get("reason", ""),
                }
                bd_alias_map[bd_alias] = p_data
                bd_id_to_alias[bd_id] = bd_alias
            else:
                p_data = dict(bd_alias_map[bd_alias])
                p_data["relation"] = m.get("relation", "realizes")
                p_data["confidence"] = m.get("confidence")
                p_data["reason"] = m.get("reason", "")
            bd_proposals.append(p_data)

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
                bd_candidates=bd_candidates,
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
    # Verifier-kept ids that no mapper row backs are the verifier's OWN mapping claim; they are
    # persisted so the report can show the mapping that BD-side coverage is asserted from.
    synthesized_mappings: list[tuple[str, str, str, str, str, str, float | None, str, str]] = []

    relations_by_step: dict[str, dict[str, str]] = {}
    for s_id, m_list in mappings_by_step.items():
        for m in m_list:
            relations_by_step.setdefault(s_id, {})[m["bd_id"]] = (m.get("relation") or "").strip().lower()

    # Process in-scope steps
    for ctx in contexts:
        sid = ctx.user_step_id
        model_out = llm_results_by_step.get(sid)
        meta = step_meta_by_step.get(sid, {})
        v_basis_bd_id: str | None = None

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

            # Only coverage-bearing relations can carry COVERED. A `related` mapping marks topical
            # adjacency (and is the raw material for CONTRADICTED) — it never asserts that the BD
            # unit realizes the step, so it must not be able to mark the step or the unit covered.
            step_relations = relations_by_step.get(sid, {})
            coverage_kept = [
                b_id for b_id in v_kept
                if step_relations.get(b_id, "realizes") in COVERAGE_RELATIONS
            ]

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
            # Fusion Rule 4 (UP3-3 invariant): COVERED <=> >=1 kept coverage-bearing BD mapping AND
            # (own valid citation OR a named basis unit, kept, whose stored verdict is MATCH/PARTIAL).
            elif v_verdict == "COVERED":
                if not coverage_kept:
                    # A COVERED with nothing kept is a malformed answer, not a finding: BD_MISSING is
                    # only ever the model's own explicit verdict (it alone can assert "no candidate
                    # fits"), so this demotes rather than inventing a divergence.
                    v_verdict = "UNVERIFIABLE"
                    v_div = None
                    v_reason = f"{v_reason} [NO_COVERAGE_BEARING_KEPT_BD]"
                elif valid_cits:
                    v_basis = "own_citation"
                else:
                    # Basis (ii): the gate — not the model — is the authority on the stored
                    # BD<->code verdict, and the model must NAME the unit it is relying on. Picking
                    # an arbitrary qualifying kept unit would bypass the same-behaviour judgment the
                    # prompt asks for (and is unsafe for PARTIAL units verified on another subclaim).
                    named_basis = model_out.basis_bd_id
                    basis_verdict = bd_verdicts_by_unit.get(named_basis or "", {}).get("verdict")
                    if not named_basis:
                        v_verdict = "UNVERIFIABLE"
                        v_div = None
                        v_reason = f"{v_reason} [NO_CITATION_NO_BASIS_BD_ID]"
                    elif named_basis not in coverage_kept:
                        v_verdict = "UNVERIFIABLE"
                        v_div = None
                        v_reason = f"{v_reason} [BASIS_BD_ID_NOT_KEPT]"
                    elif basis_verdict not in ("MATCH", "PARTIAL"):
                        v_verdict = "UNVERIFIABLE"
                        v_div = None
                        v_reason = f"{v_reason} [BASIS_BD_VERDICT_{basis_verdict or 'UNKNOWN'}]"
                    else:
                        v_basis = "bd_verdict"
                        v_basis_bd_id = named_basis

        # BD-side coverage comes ONLY from coverage-bearing kept mappings of a COVERED step.
        if v_verdict == "COVERED":
            surviving_kept_bd_ids.update(
                b_id for b_id in v_kept
                if relations_by_step.get(sid, {}).get(b_id, "realizes") in COVERAGE_RELATIONS
            )

        # A kept id the mapper never proposed is the verifier's own correction; persist it so the
        # report renders the mapping that BD-side coverage and the branch rollup are claimed from.
        for b_id in v_kept:
            if b_id in relations_by_step.get(sid, {}):
                continue
            unit = bd_unit_by_id.get(b_id)
            synthesized_mappings.append((
                f"ubmv:{new_id()}",
                run_id,
                sid,
                unit.bd_kind if unit else "step",
                b_id,
                "realizes",
                None,
                f"[verifier-added] {v_reason}",
                now,
            ))
            relations_by_step.setdefault(sid, {})[b_id] = "realizes"

        final_step_verdicts[sid] = v_verdict

        ev_json = json.dumps({
            "kept_bd_ids": v_kept,
            "basis_bd_id": v_basis_bd_id,
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
                "basis_bd_id": v_basis_bd_id,
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

    # Process Tier-1 skipped steps (NONE / UNRESOLVED matched activity -> UNVERIFIABLE). The two
    # are gated alike but reported apart: "no BD flow exists" is a finding, "matching failed" is not.
    unresolved_set = unresolved_step_ids or set()
    for s in tier1_skipped_steps:
        sid = s["id"]
        is_unresolved = sid in unresolved_set
        v_verdict = "UNVERIFIABLE"
        v_div = None
        v_reason = (
            "TIER1_UNRESOLVED: Step skipped Tier-2 alignment because Tier-1 matching did not resolve for its activity."
            if is_unresolved
            else "TIER1_NONE_MATCH: Step skipped Tier-2 alignment because its activity has no matching BD flow."
        )
        final_step_verdicts[sid] = v_verdict

        ev_json = json.dumps({
            "kept_bd_ids": [],
            "basis": "tier1_unresolved" if is_unresolved else "tier1_none_match",
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

    # 12. BD-Side Verdicts (BD_EXTRA vs BD_UNMAPPED vs COVERED - Codex R2/R5)
    bd_verdicts_to_insert: list[tuple[str, str, str, str, str, str, str, str, str, str | None, str, str, str]] = []

    # Get Tier-1 match status per flow in current run
    async with db.execute(
        "SELECT activity_id, bd_flow_id, match_status FROM user_activity_matches WHERE run_id = ?",
        (run_id,),
    ) as cur:
        act_match_rows = await cur.fetchall()

    matched_tier1_flow_ids: set[str] = set()
    unresolved_tier1_flow_ids: set[str] = set()
    # An activity whose tier-1 matching failed carries no flow id, so it makes the ABSENCE of a
    # pair unusable as evidence: nothing in the catalog can honestly be called BD_EXTRA that run.
    tier1_unresolved_globally = False
    for r in act_match_rows:
        bf_id = r[1] if isinstance(r, (tuple, list)) else r["bd_flow_id"]
        m_stat = r[2] if isinstance(r, (tuple, list)) else r["match_status"]
        if m_stat == "UNRESOLVED":
            tier1_unresolved_globally = True
        if bf_id:
            if m_stat in ("FULLY", "PARTIAL"):
                matched_tier1_flow_ids.add(bf_id)
            elif m_stat == "UNRESOLVED":
                unresolved_tier1_flow_ids.add(bf_id)

    # BD-side must enumerate the FULL cluster catalog
    bd_side_ctx = bd_ctx
    if step_bd_scope:
        bd_side_ctx = await load_bd_context(db, cluster_id)

    bd_extra_count = 0
    bd_unmapped_count = 0
    bd_unassessed_count = 0

    for u in bd_side_ctx.units:
        # Strict: BD unit is COVERED only if >=1 verifier-kept mapping reached it
        is_covered = u.unit_id in surviving_kept_bd_ids

        if is_covered:
            bd_side_verdict = "COVERED"
            bd_side_div = None
            bd_side_reason = "Referenced and verified by user flow step."
        else:
            if u.flow_id in matched_tier1_flow_ids:
                bd_side_verdict = "BD_UNMAPPED"
                bd_side_div = None
                bd_side_reason = "Parent flow matched in Tier-1, but no user step mapped to this specific unit."
                bd_unmapped_count += 1
            elif u.flow_id in unresolved_tier1_flow_ids or tier1_unresolved_globally:
                bd_side_verdict = "UNRESOLVED"
                bd_side_div = None
                bd_side_reason = "Tier-1 matching did not resolve for this run; BD-side status unknown."
                bd_unassessed_count += 1
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
            json.dumps({"name": u.name, "description": u.description, "flow_name": u.flow_name}, ensure_ascii=False),
            now,
        ))

    # 13. Replace user_verdicts (and this stage's own synthesized mappings) for this run_id
    await db.execute(
        "DELETE FROM user_verdicts WHERE run_id = ?",
        (run_id,),
    )
    for sm in synthesized_mappings:
        await db.execute(
            "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sm,
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
        bd_unmapped_count=bd_unmapped_count,
        bd_unassessed_count=bd_unassessed_count,
        flow_verdicts=final_flow_verdicts,
        step_verdicts=final_step_verdicts,
        activity_verdicts=final_activity_verdicts,
        llm_batches_issued=llm_batches_issued,
        physical_attempts=total_physical_attempts,
    )
