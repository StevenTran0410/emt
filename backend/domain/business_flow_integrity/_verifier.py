"""Source-aware unit verifier + immutable run artifacts for Phase 6 (TICKET P6).

3-LLM matching pipeline:
1. Deterministic extract: _resolve_unit_rel_paths gives seed candidate rel_paths.
2. LLM #1 Enrich + Verify seed: enrich_unit_bindings runs on ALL units to confirm/correct/enrich backing files.
3. BFS source expansion: expand_unit_snippets walks code_flow_edges 1 hop to gather expanded snippets.
4. LLM #2 Matcher: run_unit_matching finds matching snippets and produces locations-only citations.
5. LLM #3 Verifier: _evaluate_verifier_batch judges exact fetched source for citations and outputs final verdict + aspects.
Deterministic fusion resolves citations, applies fusion rules + contradiction floor, and persists verdicts + run artifacts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace as dataclasses_replace
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from shared.logger import logger
from shared.utils import new_id, utc_now_iso

from ..doc_graph._llm_citation import _parse_llm_json
from ..model_connector.service import ProviderConfigService
from ..model_connector.types import ChatMessage, ChatRequest
from ._citation import ResolvedCitation, resolve_citation
from ._enrich import EnrichResult, enrich_unit_bindings
from ._llm import LLM_CONCURRENCY, assert_exact_id_coverage, call_with_reasoning_ladder, coerce_results_wrapper
from ._matching import MatchResult, _evaluate_matcher_batch, run_unit_matching
from ._resolve import resolve_asset
from ._retrieval import Snippet, expand_unit_snippets, retrieve_unit_snippets

# Bump on any change to prompts or response schemas — stored in every artifact.
PROMPT_VERSION = "p6-fix-corrector-v1"

_BATCH_SIZE = 5

StoredVerdict = Literal["MATCH", "PARTIAL", "BROKEN", "UNKNOWN"]
AspectValue = Literal["YES", "NO", "INSUFFICIENT", "NOT_APPLICABLE"]
LLMVerdict = Literal["MATCH", "PARTIAL", "BROKEN", "INSUFFICIENT_EVIDENCE"]


class LLMAspects(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    target_reachable: AspectValue
    guard_equivalence: AspectValue
    route_order: AspectValue
    negative_modality: AspectValue


class LLMCitation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    rel_path: str
    line_start: int
    line_end: int


class LLMUnitVerification(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    subclaims: list[str]
    aspects: LLMAspects
    citations: list[LLMCitation]
    verdict: LLMVerdict
    reason_codes: list[str]
    reason: str


class LLMBatchVerificationResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMUnitVerification]


_VERIFIER_SYSTEM_PROMPT = """You are the FINAL verifier and corrector for mainframe codebase business flow integrity. \
For each business unit (a BD step or branch), you are given: the BD claim (`prose`), the candidate source `snippets` \
that were shown to the previous agent (the matcher), and that matcher's `matcher_proposal` (its `maps_to_code`, \
`citations`, `subclaims`, `reason`) — which may be null.

Treat `matcher_proposal` as an unverified HYPOTHESIS, never as truth. Re-judge independently from the snippets:
(a) If the proposal is correct, confirm it and cite the same correct location.
(b) If the matcher is WRONG — it cited the wrong file or wrong lines, made a copy/typo error in a citation, \
over-claimed a MATCH, or MISSED a snippet that CONTRADICTS the BD (e.g. a menu option / guard / route that maps \
differently than the BD states) — you must CORRECT it: emit your own verdict with your OWN citations.
(c) If `maps_to_code` is false but a snippet DOES realize or DOES contradict the unit, override it.
Your verdict is final. Citations MUST point only inside the provided snippets; the system re-fetches the verbatim \
bytes and rejects anything outside the shown window.

Work strictly and literally. Judge ONLY what the snippets actually show. Do NOT speculate, do NOT infer \
behavior that is not written in the snippet, do NOT bring in outside knowledge of program names or business \
domain. Evaluate each aspect independently, one at a time — do not let one aspect's answer sway another.

CRITICAL — separate "absent" from "contradicted" (this is the most common mistake):
 - INSUFFICIENT = the snippet simply does not contain the claimed behavior; you cannot see it here. Absence of \
evidence is ALWAYS INSUFFICIENT, never NO.
 - NO = the snippet AFFIRMATIVELY shows the code doing something DIFFERENT from, or contradictory to, the BD \
claim (the guard tests a different condition, control routes to a different target, or the BD says X does not \
happen but the snippet shows it does).

Rules:
1. Use ONLY the given snippets as evidence.
2. Citations are LOCATIONS ONLY (rel_path + line_start + line_end) — the system fetches the exact verbatim \
quote from the pinned file; do not paraphrase or invent quoted text in a citation.
3. Break the unit's claim into short atomic subclaims, then judge these four tri-state aspects, each on its own, \
applying the absent-vs-contradicted rule above:
   - target_reachable: YES = a snippet shows the target program/step being invoked/reached; NO = a snippet shows \
a DIFFERENT target reached instead; INSUFFICIENT = the invocation is not shown either way.
   - guard_equivalence: YES = the guard in the snippet matches the BD guard class (e.g. success vs error, same \
option set); NO = the snippet's guard tests a MATERIALLY DIFFERENT condition than the BD; INSUFFICIENT = the \
guard is not present in the snippet; NOT_APPLICABLE = the unit has no guard.
   - route_order: YES = order shown is consistent with the BD; NO = the snippet shows a CONFLICTING order; \
INSUFFICIENT = order is not observable in the snippet.
   - negative_modality: only when the BD asserts something does NOT happen — YES = the snippet supports that \
negative claim; NO = the snippet shows that thing DOES happen; NOT_APPLICABLE = the BD makes no negative claim.
4. Choose the verdict — EXACTLY one of "MATCH", "PARTIAL", "BROKEN", "INSUFFICIENT_EVIDENCE":
   - MATCH: real snippet support with a valid citation and no aspect judged NO.
   - BROKEN: ONLY when a snippet POSITIVELY contradicts the BD — that means guard_equivalence = NO or \
negative_modality = NO. NEVER output BROKEN merely because the claimed behavior is absent from the snippet — \
that is INSUFFICIENT_EVIDENCE.
   - INSUFFICIENT_EVIDENCE: the snippets neither confirm nor contradict the claim. Prefer this over guessing.
   - PARTIAL: partially supported but incomplete.
   YES / NO / INSUFFICIENT / NOT_APPLICABLE are ONLY aspect values — NEVER put YES, NO, NO_MATCH, or any aspect \
value in the `verdict` field.
5. Every unit_id given to you MUST appear EXACTLY ONCE in your results — no omissions, no duplicates, no extra ids.
6. Respond ONLY in English. Output ONLY a single JSON object with EXACTLY this top-level shape — no wrapper \
object, no renamed keys, no extra keys:
{
  "results": [
    {
      "unit_id": "u1",
      "subclaims": ["The step routes the user to the coil entry screen."],
      "aspects": {
        "target_reachable": "YES",
        "guard_equivalence": "NOT_APPLICABLE",
        "route_order": "YES",
        "negative_modality": "NOT_APPLICABLE"
      },
      "citations": [{"rel_path": "HSBMENU5.pfd", "line_start": 12, "line_end": 18}],
      "verdict": "MATCH",
      "reason_codes": ["target_reachable_confirmed"],
      "reason": "Snippet shows the menu option routing directly to PHNIXLOT."
    }
  ]
}
"""


@dataclass(frozen=True)
class SourceAwareVerdictResult:
    cluster_id: str
    snapshot_id: str
    run_id: str
    total_units: int
    match_count: int
    partial_count: int
    broken_count: int
    insufficient_count: int  # stored as verdict UNKNOWN
    llm_batches_issued: int


async def _evaluate_verifier_batch(
    batch_units: list[dict[str, Any]],
    snippets_by_unit: dict[str, list[Snippet]],
    matcher_results: dict[str, MatchResult],
    provider_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, LLMUnitVerification], dict[str, Any]]:
    """Execute one batched source-aware verifier LLM call (<=5 units) with short alias IDs and matcher proposals."""
    async with semaphore:
        alias_of = {u["unit_id"]: f"u{i+1}" for i, u in enumerate(batch_units)}
        real_of = {f"u{i+1}": u["unit_id"] for i, u in enumerate(batch_units)}
        expected_aliases = set(alias_of.values())

        prompt_payload = []
        for u in batch_units:
            real_uid = u["unit_id"]
            m = matcher_results.get(real_uid)
            matcher_proposal = (
                {
                    "maps_to_code": m.maps_to_code,
                    "citations": m.citations,
                    "subclaims": m.subclaims,
                    "reason": m.reason,
                }
                if m else None
            )
            prompt_payload.append({
                "unit_id": alias_of[real_uid],
                "unit_kind": u["unit_kind"],
                "prose": u["prose"],
                "doc_line_start": u.get("doc_line_start"),
                "doc_line_end": u.get("doc_line_end"),
                "snippets": [
                    {
                        "rel_path": s.rel_path,
                        "line_start": s.line_start,
                        "line_end": s.line_end,
                        "text": s.text,
                        "parse_status": s.parse_status,
                    }
                    for s in snippets_by_unit.get(real_uid, [])
                ],
                "matcher_proposal": matcher_proposal,
            })

        attempts = {"n": 0}

        def _build_req(effort: str) -> ChatRequest:
            attempts["n"] += 1
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_VERIFIER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMUnitVerification]:
            parsed = coerce_results_wrapper(_parse_llm_json(text))
            validated = LLMBatchVerificationResponse.model_validate(parsed)
            assert_exact_id_coverage([r.unit_id for r in validated.results], expected_aliases, "BFI verifier")
            return {r.unit_id: r for r in validated.results}

        start = time.monotonic()
        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse, first_effort="high", label="BFI source-aware verifier (LLM#3)"
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        meta = {"retry_count": max(attempts["n"] - 1, 0), "latency_ms": latency_ms}

        if ladder_res is None:
            return {}, meta

        parsed_items, _ = ladder_res
        results: dict[str, LLMUnitVerification] = {}
        for alias_id, item in parsed_items.items():
            real_uid = real_of.get(alias_id, alias_id)
            results[real_uid] = item
        return results, meta


# BD authors pack several backing files into one node binding — "HNIXLOT / FHNIXLOT",
# "PHNIXLOT → HNDM001N", "HNDM001N / HNDX001N / HNDM130" — so a literal lookup of the whole
# string misses real files. Split on the separators the BD used, then resolve each part.
_BINDING_SPLIT_RE = re.compile(r"\s*(?:/|→|->|,|\+)\s*")
# BD names UI events like "HSBMENU5-Event-Init" / "FHNIXLOT-Event-FKey-F1"; the backing file is
# the owning screen (prefix before the event suffix).
_EVENT_SUFFIX_RE = re.compile(r"-Event-|-FKey-")


def _resolve_unit_rel_paths(
    source_nids: list[str], bd_nodes_map: dict[str, dict[str, Any]], manifest_paths: list[str]
) -> list[str]:
    """Resolve a unit's BD binding tokens to manifest rel_paths (RESOLVED only), splitting the
    compound / event-suffixed bindings BD authors use so real backing files are not falsely missed."""
    rel_paths: list[str] = []
    for nid in source_nids:
        bd_node = bd_nodes_map.get(nid)
        binding = bd_node.get("binding") if bd_node else None
        if not binding:
            continue
        btype = bd_node.get("binding_type")

        # FIX 8: try the whole binding first — splitting unconditionally shreds a legitimate
        # relative path like "dir1/PROG.cbl" and can even grab the wrong same-basename file.
        whole = resolve_asset(binding.strip(), btype, manifest_paths)
        if whole.status == "RESOLVED" and whole.rel_path:
            if whole.rel_path not in rel_paths:
                rel_paths.append(whole.rel_path)
            continue

        for part in _BINDING_SPLIT_RE.split(binding):
            part = part.strip()
            if not part:
                continue
            stem = _EVENT_SUFFIX_RE.split(part)[0].strip()
            for cand in (part, stem):
                res = resolve_asset(cand, btype, manifest_paths)
                if res.status == "RESOLVED" and res.rel_path and res.rel_path not in rel_paths:
                    rel_paths.append(res.rel_path)
                    break
    return rel_paths


async def run_source_aware_verdicts(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> SourceAwareVerdictResult:
    """Run Phase 6 source-aware unit verdicts: enrich -> BFS expand -> match -> verify (corrector) -> fusion -> persist."""
    # Local import (not module-level) so `provider_id=None` deterministic mapping stays a single call
    # site that test monkeypatching of domain.business_flow_integrity._map.run_unit_mapping still sees.
    from ._map import run_unit_mapping

    run_id = new_id()

    # 1. Deterministic mapping pass (provider_id=None -> no LLM inside) — reused ONLY for the
    # contradicted_nodes signal that drives the kept contradiction floor (rule 3).
    mappings = await run_unit_mapping(db, cluster_id, snapshot_id, provider_id=None)

    if not mappings:
        await db.execute(
            "DELETE FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?", (cluster_id, snapshot_id)
        )
        await db.commit()
        return SourceAwareVerdictResult(cluster_id, snapshot_id, run_id, 0, 0, 0, 0, 0, 0)

    async with db.execute("SELECT id FROM bd_business_flows WHERE cluster_id=?", (cluster_id,)) as cur:
        flow_ids = [r["id"] for r in await cur.fetchall()]

    flow_ids_str = ",".join("?" for _ in flow_ids) if flow_ids else "''"

    async with db.execute(
        f"SELECT id, flow_id, name, functionality, source_node_ids, doc_line_start, doc_line_end FROM bd_business_steps WHERE flow_id IN ({flow_ids_str})",
        flow_ids,
    ) as cur:
        step_rows = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        f"SELECT id, flow_id, source_step_id, target_step_id, branch_kind, guard_description, source_edge_ids FROM bd_business_branches WHERE flow_id IN ({flow_ids_str})",
        flow_ids,
    ) as cur:
        branch_rows = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        "SELECT id, binding, binding_type FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,)
    ) as cur:
        bd_nodes_map = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute("SELECT id, doc_line FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,)) as cur:
        bd_edges_map = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute("SELECT rel_path FROM manifest_files WHERE snapshot_id=?", (snapshot_id,)) as cur:
        manifest_paths = [r["rel_path"] for r in await cur.fetchall()]

    # 2. Assemble the unit list (prose + BD provenance + deterministic candidate rel_paths + contradiction flag).
    units: list[dict[str, Any]] = []
    for m in mappings:
        uid = m.unit_id
        if m.unit_kind == "step":
            row = step_rows.get(uid)
            if not row:
                continue
            prose = f"{row['name']}: {row['functionality']}"
            source_nids = json.loads(row.get("source_node_ids") or "[]")
            doc_line_start = row.get("doc_line_start")
            doc_line_end = row.get("doc_line_end")
        else:
            row = branch_rows.get(uid)
            if not row:
                continue
            prose = f"Branch ({row['branch_kind']}): {row['guard_description']}"
            src_step = step_rows.get(row.get("source_step_id"), {})
            dst_step = step_rows.get(row.get("target_step_id") or "", {})
            source_nids = list(set(
                json.loads(src_step.get("source_node_ids") or "[]")
                + json.loads(dst_step.get("source_node_ids") or "[]")
            ))
            edge_ids = json.loads(row.get("source_edge_ids") or "[]")
            doc_lines = [
                bd_edges_map[eid]["doc_line"]
                for eid in edge_ids
                if eid in bd_edges_map and bd_edges_map[eid].get("doc_line") is not None
            ]
            doc_line_start = min(doc_lines) if doc_lines else None
            doc_line_end = max(doc_lines) if doc_lines else None

        rel_paths = _resolve_unit_rel_paths(source_nids, bd_nodes_map, manifest_paths)
        first_nid = source_nids[0] if source_nids else None
        bd_node = bd_nodes_map.get(first_nid, {}) if first_nid else {}

        units.append({
            "unit_id": uid,
            "flow_id": row["flow_id"],
            "unit_kind": m.unit_kind,
            "prose": prose,
            "label": bd_node.get("label"),
            "binding": bd_node.get("binding"),
            "binding_type": bd_node.get("binding_type"),
            "doc_line_start": doc_line_start,
            "doc_line_end": doc_line_end,
            "rel_paths": rel_paths,
            "seed_rel_paths": list(rel_paths),  # Change D.1: Snapshot seed rel_paths
            "contradicted": bool(m.contradicted_nodes),
            "contradicted_nodes": m.contradicted_nodes,
        })

    # Compute per-flow deterministic file set before enrich overwrites rel_paths
    flow_rel_paths: dict[str, set[str]] = {}
    for u in units:
        flow_rel_paths.setdefault(u["flow_id"], set()).update(u.get("seed_rel_paths") or [])

    llm_batches_issued = 0

    # 2b. LLM #1 — Enrich + Verify seed on ALL units
    enrich_resolutions: dict[str, EnrichResult] = {}
    if provider_id:
        enrich_resolutions = await enrich_unit_bindings(
            db, snapshot_id, units, manifest_paths, provider_id
        )
        llm_batches_issued += (len(units) + _BATCH_SIZE - 1) // _BATCH_SIZE
        for u in units:
            e_res = enrich_resolutions.get(u["unit_id"])
            if e_res and e_res.confirmed_rel_paths:
                u["rel_paths"] = list(e_res.confirmed_rel_paths)
            elif e_res and e_res.no_file:
                u["rel_paths"] = []

    # 3. Deterministic BFS source expansion (Phase 6 expand_unit_snippets) with flow-level fallback
    abstained: dict[str, str] = {}
    expanded_snippets_by_unit: dict[str, list[Snippet]] = {}
    ready_for_match_units: list[dict[str, Any]] = []

    for u in units:
        eff_rel_paths = u["rel_paths"]
        if not eff_rel_paths:
            # Unit's own files are empty (unresolved binding, or enrich said no_file).
            # Fall back to the flow's deterministically-resolved files so the unit is still
            # retrieved + matched + verified. The verifier remains the authority on evidence.
            flow_files = sorted(flow_rel_paths.get(u["flow_id"], set()))
            if flow_files:
                eff_rel_paths = flow_files
                u["retrieval_flow_fallback"] = True

        retrieval = await expand_unit_snippets(
            db, snapshot_id, {"unit_id": u["unit_id"], "unit_kind": u["unit_kind"]}, rel_paths=eff_rel_paths
        )
        if retrieval.abstain_reason:
            e_res = enrich_resolutions.get(u["unit_id"])
            # Only a TRUE no-backing verdict: enrich said no_file AND the flow itself has no files at all.
            if e_res and e_res.no_file and not flow_rel_paths.get(u["flow_id"]):
                abstained[u["unit_id"]] = "NO_FILE_IN_SNAPSHOT"
            else:
                abstained[u["unit_id"]] = retrieval.abstain_reason
        else:
            expanded_snippets_by_unit[u["unit_id"]] = retrieval.snippets
            ready_for_match_units.append(u)

    # 4. LLM #2 — Matching pass on ready units
    match_results: dict[str, MatchResult] = {}
    match_meta_by_unit: dict[str, dict[str, Any]] = {}

    if provider_id and ready_for_match_units:
        batches = [ready_for_match_units[i : i + _BATCH_SIZE] for i in range(0, len(ready_for_match_units), _BATCH_SIZE)]
        semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
        tasks = [_evaluate_matcher_batch(b, expanded_snippets_by_unit, provider_id, semaphore) for b in batches]
        batch_outputs = await asyncio.gather(*tasks, return_exceptions=True)

        for batch, out in zip(batches, batch_outputs):
            llm_batches_issued += 1
            if isinstance(out, Exception):
                logger.warning(f"BFI matcher batch failed: {out!r}")
                continue
            b_res, meta = out
            match_results.update(b_res)
            for u in batch:
                match_meta_by_unit[u["unit_id"]] = meta

    # 5. Prepare input for LLM #3 Verifier (Corrector)
    # Change A: Every unit in ready_for_match_units that has expanded snippets reaches the verifier
    ready_for_verifier_units: list[dict[str, Any]] = []
    verifier_snippets_by_unit: dict[str, list[Snippet]] = {}
    matcher_resolved_citations_by_unit: dict[str, list[ResolvedCitation]] = {}

    for u in ready_for_match_units:
        uid = u["unit_id"]
        m_res = match_results.get(uid)

        # Still resolve matcher's proposed citations for proposal/artifact audit
        resolved_cits: list[ResolvedCitation] = []
        if m_res and m_res.citations:
            for c in m_res.citations:
                rc = await resolve_citation(db, snapshot_id, c["rel_path"], c["line_start"], c["line_end"])
                # Verify citation lies inside the expanded snippet window shown to the matcher
                if rc.valid and not any(
                    s.rel_path == rc.rel_path and rc.line_start >= s.line_start and rc.line_end <= s.line_end
                    for s in expanded_snippets_by_unit.get(uid, [])
                ):
                    rc = dataclasses_replace(rc, valid=False, reject_reason="CITATION_OUT_OF_WINDOW")
                resolved_cits.append(rc)
        matcher_resolved_citations_by_unit[uid] = resolved_cits

        snippets = expanded_snippets_by_unit.get(uid, [])
        if snippets:
            verifier_snippets_by_unit[uid] = snippets
            ready_for_verifier_units.append(u)

    # 5b. LLM #3 — Batched Verifier pass (Corrector)
    llm_results: dict[str, LLMUnitVerification] = {}
    verifier_meta_by_unit: dict[str, dict[str, Any]] = {}

    if provider_id and ready_for_verifier_units:
        batches = [ready_for_verifier_units[i : i + _BATCH_SIZE] for i in range(0, len(ready_for_verifier_units), _BATCH_SIZE)]
        semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
        tasks = [_evaluate_verifier_batch(b, verifier_snippets_by_unit, match_results, provider_id, semaphore) for b in batches]
        batch_outputs = await asyncio.gather(*tasks, return_exceptions=True)

        for batch, out in zip(batches, batch_outputs):
            llm_batches_issued += 1
            if isinstance(out, Exception):
                logger.warning(f"BFI source-aware verifier batch failed: {out!r}")
                continue
            batch_results, meta = out
            llm_results.update(batch_results)
            for u in batch:
                verifier_meta_by_unit[u["unit_id"]] = meta

    resolved_model_id: str | None = None
    if provider_id:
        try:
            config = await ProviderConfigService().get_by_id(provider_id)
            resolved_model_id = config.model_id
        except Exception as e:
            logger.warning(f"BFI source-aware verifier: could not resolve model_id for provider {provider_id}: {e}")

    # 6. Deterministic fusion per unit + deterministic contradiction floor (final override).
    unit_records: list[tuple] = []
    artifact_rows: list[tuple] = []
    now = utc_now_iso()

    for u in units:
        uid = u["unit_id"]
        e_res = enrich_resolutions.get(uid)
        m_res = match_results.get(uid)
        model_output = llm_results.get(uid)

        citations_resolved: list[ResolvedCitation] = []
        reason_codes: list[str] = []
        guard_verdict: str | None = None
        ai_bucket: str | None = None

        if uid in abstained:
            verdict: str = "INSUFFICIENT_EVIDENCE"
            reason_codes = [abstained[uid]]
            if abstained[uid] == "NO_FILE_IN_SNAPSHOT" and e_res:
                reason_text = f"BD unit classified as no backing file in snapshot: {e_res.reason}"
            else:
                reason_text = f"Source retrieval abstained: {abstained[uid]}."
        elif model_output is None:
            verdict = "INSUFFICIENT_EVIDENCE"
            if not provider_id:
                reason_codes = ["OFFLINE_NO_LLM"]
                reason_text = "Offline run (no provider) — deterministic retrieval succeeded but no LLM verification was requested."
            else:
                reason_codes = ["LLM_NO_RESPONSE"]
                reason_text = "LLM verifier did not return a parseable result for this unit."
        else:
            # Check verifier's citations
            for c in model_output.citations:
                rc = await resolve_citation(db, snapshot_id, c.rel_path, c.line_start, c.line_end)
                # Verify containment against snippets shown to verifier
                shown_snippets = verifier_snippets_by_unit.get(uid, []) or expanded_snippets_by_unit.get(uid, [])
                if rc.valid and not any(
                    s.rel_path == rc.rel_path and rc.line_start >= s.line_start and rc.line_end <= s.line_end
                    for s in shown_snippets
                ):
                    rc = dataclasses_replace(rc, valid=False, reject_reason="CITATION_OUT_OF_WINDOW")
                citations_resolved.append(rc)

            any_invalid = any(not rc.valid for rc in citations_resolved)
            valid_citations = [rc for rc in citations_resolved if rc.valid]
            has_no_aspect = any(v == "NO" for v in model_output.aspects.model_dump().values())

            if model_output.aspects.guard_equivalence == "YES":
                guard_verdict = "CLASS_MATCH"
            elif model_output.aspects.guard_equivalence == "NO":
                guard_verdict = "CLASS_MISMATCH"

            if any_invalid:
                # Rule 1: ANY invalid citation forces INSUFFICIENT_EVIDENCE
                verdict = "INSUFFICIENT_EVIDENCE"
                reason_codes = list(model_output.reason_codes) + ["CITATION_INVALID"]
                if any(rc.reject_reason == "CITATION_OUT_OF_WINDOW" for rc in citations_resolved):
                    reason_codes.append("CITATION_OUT_OF_WINDOW")
                reason_text = model_output.reason
            elif model_output.verdict in ("MATCH", "PARTIAL", "BROKEN") and not valid_citations:
                # Rule 2: any resolved verdict needs >=1 valid citation
                verdict = "INSUFFICIENT_EVIDENCE"
                reason_codes = list(model_output.reason_codes) + ["NO_CITATION"]
                reason_text = model_output.reason
            elif model_output.verdict == "BROKEN" and not has_no_aspect:
                # Rule 3: BROKEN requires a POSITIVE contradiction signal (some aspect == NO)
                verdict = "INSUFFICIENT_EVIDENCE"
                reason_codes = list(model_output.reason_codes) + ["BROKEN_NO_CONTRADICTION_ASPECT"]
                reason_text = model_output.reason
            elif model_output.verdict == "MATCH" and has_no_aspect:
                # Rule 4: MATCH requires no aspect judged NO
                verdict = "PARTIAL"
                reason_codes = list(model_output.reason_codes) + ["ASPECT_CONTRADICTION"]
                reason_text = model_output.reason
            else:
                # Rule 5: as-is
                verdict = model_output.verdict
                reason_codes = list(model_output.reason_codes)
                reason_text = model_output.reason

        # Deterministic contradiction floor (kept, final override)
        if u["contradicted"]:
            verdict = "BROKEN"
            ai_bucket = "stale_missing"
            if "CONTRADICTION_FLOOR" not in reason_codes:
                reason_codes = reason_codes + ["CONTRADICTION_FLOOR"]
            c_node = u["contradicted_nodes"][0] if u["contradicted_nodes"] else {}
            c_file = c_node.get("rel_path") or c_node.get("binding")
            reason_text = f"BD unit claims missing/unresolved asset ({c_file}), contradicted by code graph"

        stored_verdict: StoredVerdict = "UNKNOWN" if verdict == "INSUFFICIENT_EVIDENCE" else verdict  # type: ignore[assignment]
        mapping_status = "MAPPED" if uid not in abstained else "NO_SAFE_MATCH"
        mapping_method = "source_aware_llm" if (model_output is not None) else "source_aware_offline"

        # Compute verifier_corrected and matcher_maps_to_code
        matcher_maps_to_code = m_res.maps_to_code if m_res else None
        verifier_corrected = False
        if m_res is not None and model_output is not None:
            if not m_res.maps_to_code:
                # Matcher said false, but verifier produced a resolved verdict with valid citations
                if verdict in ("MATCH", "PARTIAL", "BROKEN") and valid_citations:
                    verifier_corrected = True
            else:
                matcher_cits = {(c["rel_path"], c["line_start"], c["line_end"]) for c in m_res.citations}
                verifier_cits = {(c.rel_path, c.line_start, c.line_end) for c in model_output.citations}
                if verdict == "BROKEN" or verifier_cits != matcher_cits:
                    verifier_corrected = True

        evidence_dict = {
            "reason_codes": reason_codes,
            "run_id": run_id,
            "model_verdict": model_output.verdict if model_output else None,
            "citations": [
                {
                    "rel_path": rc.rel_path,
                    "line_start": rc.line_start,
                    "line_end": rc.line_end,
                    "valid": rc.valid,
                    "reject_reason": rc.reject_reason,
                }
                for rc in citations_resolved
            ],
        }

        unit_records.append((
            f"unit_verdict:{cluster_id}:{uid}",
            cluster_id,
            snapshot_id,
            uid,
            u["unit_kind"],
            mapping_status,
            mapping_method,
            None,  # route_segment_json
            stored_verdict,
            guard_verdict,
            ai_bucket,
            reason_text,
            json.dumps(evidence_dict),
            3,  # comparator_version
            now,
        ))

        v_meta = verifier_meta_by_unit.get(uid, {})
        payload = {
            "prompt_version": PROMPT_VERSION,
            "unit_kind": u["unit_kind"],
            "unit_prose": u["prose"],
            "doc_line_start": u["doc_line_start"],
            "doc_line_end": u["doc_line_end"],
            "enrich": {
                "agent_raw": e_res.agent_raw if e_res else None,
                "seed_rel_paths": u.get("seed_rel_paths", []),
                "confirmed_rel_paths": e_res.confirmed_rel_paths if e_res else [],
                "no_file": e_res.no_file if e_res else False,
                "reason": e_res.reason if e_res else None,
                "retry_count": e_res.retry_count if e_res else 0,
            } if e_res else None,
            "match": {
                "agent_raw": m_res.agent_raw if m_res else None,
                "subclaims": m_res.subclaims if m_res else [],
                "citations": m_res.citations if m_res else [],
                "maps_to_code": m_res.maps_to_code if m_res else False,
                "reason": m_res.reason if m_res else None,
                "retry_count": m_res.retry_count if m_res else 0,
            } if m_res else None,
            "binding_resolution": None,
            "retrieval_flow_fallback": bool(u.get("retrieval_flow_fallback", False)),
            "verifier_corrected": verifier_corrected,
            "matcher_maps_to_code": matcher_maps_to_code,
            "snippet_refs": [
                {
                    "occurrence_id": s.occurrence_id,
                    "rel_path": s.rel_path,
                    "line_start": s.line_start,
                    "line_end": s.line_end,
                    "source_sha256": s.source_sha256,
                    "parse_status": s.parse_status,
                }
                for s in expanded_snippets_by_unit.get(uid, [])
            ],
            "abstain_reason": abstained.get(uid),
            "model_raw_output": model_output.model_dump() if model_output else None,
            "citation_resolutions": [
                {
                    "rel_path": rc.rel_path,
                    "line_start": rc.line_start,
                    "line_end": rc.line_end,
                    "valid": rc.valid,
                    "fetched_text": rc.fetched_text,
                    "source_sha256": rc.source_sha256,
                    "reject_reason": rc.reject_reason,
                }
                for rc in citations_resolved
            ],
            "fused_verdict": verdict,
            "reason_codes": reason_codes,
            "provider_id": provider_id,
            "model_id": resolved_model_id,
            "retry_count": v_meta.get("retry_count", 0),
            "token_usage": None,
            "latency_ms": v_meta.get("latency_ms"),
        }

        artifact_rows.append((
            f"bfart:{run_id}:{uid}", run_id, cluster_id, snapshot_id, uid, json.dumps(payload), now,
        ))

    # 7. Persist unit verdicts (delete+replace by scope)
    await db.execute(
        "DELETE FROM business_unit_verdicts WHERE cluster_id=? AND snapshot_id=?", (cluster_id, snapshot_id)
    )
    if unit_records:
        await db.executemany(
            "INSERT INTO business_unit_verdicts (id, cluster_id, snapshot_id, unit_id, unit_kind, mapping_status, mapping_method, route_segment_json, verdict, guard_verdict, ai_bucket, reason, evidence_json, comparator_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            unit_records,
        )
    await db.commit()

    # 8. Append-only run artifacts
    if artifact_rows:
        await db.executemany(
            "INSERT INTO bfi_run_artifacts (id, run_id, cluster_id, snapshot_id, unit_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            artifact_rows,
        )
        await db.commit()

    total = len(unit_records)
    match_cnt = sum(1 for r in unit_records if r[8] == "MATCH")
    partial_cnt = sum(1 for r in unit_records if r[8] == "PARTIAL")
    broken_cnt = sum(1 for r in unit_records if r[8] == "BROKEN")
    insufficient_cnt = sum(1 for r in unit_records if r[8] == "UNKNOWN")

    return SourceAwareVerdictResult(
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
        total_units=total,
        match_count=match_cnt,
        partial_count=partial_cnt,
        broken_count=broken_cnt,
        insufficient_count=insufficient_cnt,
        llm_batches_issued=llm_batches_issued,
    )
