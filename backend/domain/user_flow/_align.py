"""Fused Anchor + BD Mapper stage for User Flow alignment (TICKET UR + UR-FIX).

Performs fused evaluation of user steps:
1. Deterministic retrieval: retrieves immutable code snippets + BD candidate shortlist scoped by shared code files.
2. Per-flow seed retrieval scoping (no workbook-wide hit leakage across flows).
3. Fused LLM call (align_steps_batch, LOW reasoning tier): groups 8-12 steps per call; returns code claims and BD mappings with claim linkage.
4. Concurrency safety (mirrors _verifier.py): in-memory batch results gathered with return_exceptions=True, sequential DB writes, single commit.
5. Deterministic evidence-linked gate: verifies citation window containment; evidence_backed requires >=1 valid code claim; presentation steps flagged UNVERIFIABLE.
6. Persists user_code_anchors, user_bd_mappings, step audit artifacts, and reverse-index __run_summary__.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.business_flow_integrity._citation import resolve_citation
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
    cut_line_window,
    resolve_step_files,
    retrieve_step_snippets,
    seed_literal_hits,
)
from ._mapping import BDContext, BDUnit, load_bd_context


# ---------------------------------------------------------------------------
# Fused LLM Models
# ---------------------------------------------------------------------------

class FusedCitation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    snippet_id: str
    line_start: int
    line_end: int


class FusedClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claim_id: str
    citation: FusedCitation
    subclaims: list[str] = Field(default_factory=list)
    reason: str = ""


class FusedBDMapping(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bd_unit_id: str | None = None
    bd_id: str | None = None
    relation: str = "realizes"  # realizes | partial | related | member_of
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = ""
    support_claim_ids: list[str] = Field(default_factory=list)

    @property
    def target_alias(self) -> str:
        return self.bd_unit_id or self.bd_id or ""


class FusedStepResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    step_id: str  # u1, u2...
    presentation: bool = False
    reason: str = ""
    claims: list[FusedClaim] = Field(default_factory=list)
    bd_mappings: list[FusedBDMapping] = Field(default_factory=list)


class FusedAlignResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[FusedStepResult]


@dataclass
class StepAlignContext:
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
    snippets: list[Snippet]
    snippet_id_map: dict[str, Snippet]
    bd_candidates: list[BDUnit]
    bd_alias_map: dict[str, BDUnit]
    bd_id_to_alias: dict[str, str]


@dataclass
class StepAlignAudit:
    user_step_id: str
    claims: list[dict[str, Any]]
    mappings: list[dict[str, Any]]
    dropped_mappings: list[dict[str, Any]]
    dropped_unknown_bd_count: int
    evidence_backed_mappings_count: int
    raw_response: str
    latency_ms: float
    retry_count: int
    offline: bool = False


# ---------------------------------------------------------------------------
# Fused Align Prompts
# ---------------------------------------------------------------------------

_ALIGN_SYSTEM_PROMPT = """You are an expert system verifying Japanese business test/flow scenarios against Fujitsu mainframe source code and technical Business Design (BD) flow models.

For each user step, you are provided:
1. The user step's narrative text, trigger, expected behavior, and screen name.
2. A shared pool of numbered immutable source code snippets (`snippet_pool` with batch-global IDs `src1`, `src2`...) extracted from the repository. Each step lists its relevant `snippet_ids`.
3. A shortlist of technical Business Design (BD) candidate units (`bd1`, `bd2`...).

Your task is to return TWO INDEPENDENT things for each user step:

1. `claims`: Source code citations inside the provided snippets that substantiate the user step's business logic.
   - You MUST emit citations whenever the snippets contain source code implementing this user step (e.g. IF condition checks, field validation rules, error handling, calculations, screen I/O, SQL/DB operations, JCL/dataset execution).
   - Test scenario condition permutations and error test cases (e.g. "①条件≠1...", "②COIL NO≠SPACE...") that test specific logic SHOULD cite the corresponding IF statements / validation checks in the code snippets.
   - Emit code citations regardless of whether a matching BD candidate unit exists.
   - `citation`: Must specify `snippet_id` (referencing `snippet_pool`) and exact `line_start`..`line_end` (within the snippet's shown line numbers).
   - ONLY set `"presentation": true` (with empty claims) for purely visual/cosmetic presentation aspects (e.g. font size, screen layout, button color) that have no procedural backend code.

2. `bd_mappings`: Links to candidate BD units that correspond to this user step.
   - `bd_mappings` are semantic judgments comparing the user step text against the candidate catalog. You SHOULD emit bd_mappings even when `claims` is empty (source code citations are corroborating evidence, not a precondition for semantic mapping).
   - `bd_unit_id` (or `bd_id`): The alias of the matching BD candidate (`bd1`, `bd2`...).
   - `relation`:
     * "realizes": Exact match where the BD unit performs the specific business action/rule executed by this user step.
     * "partial": The step performs part of this BD unit's specific flow logic.
     * "member_of": Used specifically when the user step represents a specific test/failure instance of a guarded BD branch (e.g. user error case mapping to an ERROR/FAILURE branch). Many distinct user failure cases legitimately map to ONE error branch.
     * "related": Direct, meaningful semantic relationship to the specific BD unit.
   - `confidence`: Required float 0.0 to 1.0 (strict: only >= 0.70 for realizes, >= 0.60 for partial/related, >= 0.55 for member_of).
   - `support_claim_ids`: Optional list of `claim_id`s from above that substantiate this BD mapping with code evidence.
   - BRANCH-CLASS MATCHING RULES & ANTI-CATCH-ALL DISCIPLINE:
     * A BD branch is a guarded outcome edge. Match the user step's expected outcome message (e.g. 「前ページは存在しません」) to the branch guard ("No previous page exists.").
     * Require a shared guard CLASS (same entity/condition/outcome), not merely the same failure polarity.
     * ANTI-CATCH-ALL DISCIPLINE applies strictly to SUCCESS branches and generic initialization steps. Never map test failure variations onto a SUCCESS branch or unrelated generic step.
     * If no candidate BD unit genuinely corresponds to this user step's specific action, return `bd_mappings: []`. Unmapped steps are normal and expected for missing BD units; NEVER force-map to a generic step.

OUTPUT FORMAT:
Respond with a JSON object strictly matching this schema:
{
  "results": [
    {
      "step_id": "u1",
      "presentation": false,
      "claims": [
        {
          "claim_id": "c1",
          "citation": {
            "snippet_id": "src1",
            "line_start": 4,
            "line_end": 4
          },
          "subclaims": ["Coil condition check"],
          "reason": "IF TEST-CODE = 'COIL' validates the coil lot condition input"
        }
      ],
      "bd_mappings": [
        {
          "bd_unit_id": "bd1",
          "relation": "realizes",
          "confidence": 0.95,
          "reason": "BD step bd1 validates coil condition code matching user step",
          "support_claim_ids": ["c1"]
        }
      ]
    }
  ]
}
Every step_id from the input MUST appear exactly once in the results list.
"""


# ---------------------------------------------------------------------------
# Batch Evaluator
# ---------------------------------------------------------------------------

async def evaluate_align_batch(
    contexts: list[StepAlignContext],
    provider_id: str,
) -> tuple[dict[str, FusedStepResult], str, int, float, dict[str, Snippet]]:
    """Execute Stage F: Fused Anchor+Mapper LLM call on a batch of 8-12 steps (LOW reasoning)."""
    if not contexts:
        return {}, "", 0, 0.0, {}

    # Build shared snippet pool across all steps in the batch
    snippet_pool: list[dict[str, Any]] = []
    batch_snippet_map: dict[str, Snippet] = {}
    snip_key_to_id: dict[tuple[str, int, int], str] = {}

    for ctx in contexts:
        for snip in ctx.snippets:
            key = (snip.rel_path, snip.line_start, snip.line_end)
            if key not in snip_key_to_id:
                sid = f"src{len(snippet_pool) + 1}"
                snip_key_to_id[key] = sid
                batch_snippet_map[sid] = snip
                snippet_pool.append({
                    "snippet_id": sid,
                    "rel_path": snip.rel_path,
                    "line_start": snip.line_start,
                    "line_end": snip.line_end,
                    "source_text": snip.text,
                })

    steps_payload: list[dict[str, Any]] = []

    for ctx in contexts:
        step_snip_ids = [
            snip_key_to_id[(snip.rel_path, snip.line_start, snip.line_end)]
            for snip in ctx.snippets
        ]

        bd_payload = [
            {
                "bd_id": ctx.bd_id_to_alias.get(b.unit_id, b.alias),
                "bd_kind": b.bd_kind,
                "name": render_bd_candidate_name(b),
                "flow_name": b.flow_name or (b.name if b.bd_kind == "flow" else ""),
                "description": b.description or "",
                "verdict_summary": f"[{b.verdict}] {b.verdict_reason}" if b.verdict else "unverified",
            }
            for b in ctx.bd_candidates
        ]

        steps_payload.append({
            "step_id": ctx.step_alias,
            "kind": ctx.kind,
            "section_id": ctx.section_id,
            "text_ja": ctx.text_ja,
            "text_en": ctx.text_en,
            "trigger_ja": ctx.trigger_ja,
            "expected_ja": ctx.expected_ja,
            "screen_name_ja": ctx.screen_name_ja,
            "snippet_ids": step_snip_ids,
            "bd_candidates": bd_payload,
        })

    user_prompt = json.dumps({
        "snippet_pool": snippet_pool,
        "steps_to_align": steps_payload,
    }, ensure_ascii=False, indent=2)

    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_ALIGN_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ],
            stream=True,
            max_completion_tokens=25000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> list[FusedStepResult]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        raw_results = parsed.get("results", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])

        step_results: list[FusedStepResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            s_id = item.get("step_id") or ""
            if not s_id:
                continue

            # Parse claims tolerantly
            valid_claims: list[FusedClaim] = []
            for cl in item.get("claims", []):
                if isinstance(cl, dict):
                    try:
                        valid_claims.append(FusedClaim.model_validate(cl))
                    except Exception as e:
                        logger.debug(f"Skipping malformed claim in step {s_id}: {e}")

            # Parse mappings tolerantly
            valid_mappings: list[FusedBDMapping] = []
            for m in item.get("bd_mappings", []):
                if isinstance(m, dict):
                    try:
                        valid_mappings.append(FusedBDMapping.model_validate(m))
                    except Exception as e:
                        logger.debug(f"Skipping malformed mapping in step {s_id}: {e}")

            is_pres = bool(item.get("presentation", False)) or ("presentation" in str(item.get("reason", "")).lower())

            step_results.append(FusedStepResult(
                step_id=s_id,
                presentation=is_pres,
                reason=str(item.get("reason", "")),
                claims=valid_claims,
                bd_mappings=valid_mappings,
            ))

        expected_ids = {ctx.step_alias for ctx in contexts}
        actual_ids = [r.step_id for r in step_results]
        assert_exact_id_coverage(actual_ids, expected_ids, label="fused align")
        return step_results

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Fused Align (LLM#3 Fused)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    results_map: dict[str, FusedStepResult] = {}
    raw_text = ""

    if ladder_res is not None:
        items, raw_text = ladder_res
        for it in items:
            results_map[it.step_id] = it
    else:
        for ctx in contexts:
            results_map[ctx.step_alias] = FusedStepResult(step_id=ctx.step_alias, claims=[], bd_mappings=[])

    return results_map, raw_text, retry_count, latency_ms, batch_snippet_map


# ---------------------------------------------------------------------------
# Candidate Shortlisting by Shared Code Files & Bilingual Concepts
# ---------------------------------------------------------------------------

BILINGUAL_CONCEPT_MAP: dict[str, list[str]] = {
    "送信": ["send", "transmit", "transfer", "dispatch"],
    "転送": ["transfer", "forward", "transmit"],
    "出力": ["output", "export", "write", "print"],
    "起動": ["launch", "start", "execute", "invoke", "trigger"],
    "バッチ": ["batch", "job"],
    "指示": ["instruction", "directive", "order"],
    "エラー": ["error", "failure", "invalid", "exception", "abort"],
    "異常": ["error", "abnormal", "failure", "invalid"],
    "初期表示": ["initial display", "initial", "initialize", "load screen", "init"],
    "メニュー": ["menu", "main menu"],
    "引張": ["tensile", "tension"],
    "試験": ["test", "inspection", "trial"],
    "ロット": ["lot", "batch"],
    "更新": ["update", "modify", "save"],
    "取消": ["cancel", "abort", "revoke"],
    "削除": ["delete", "remove", "clear"],
    "クリア": ["clear", "reset"],
    "次頁": ["next page", "next screen"],
    "次ページ": ["next page", "next screen"],
    "前頁": ["previous page", "prior page", "prev page"],
    "前ページ": ["previous page", "prior page", "prev page"],
    "存在しません": ["does not exist", "not found", "no next page", "no previous page"],
    "存在しない": ["does not exist", "not found"],
    "条件": ["condition", "criteria", "parameter"],
    "入力": ["input", "enter", "entry"],
    "検証": ["validate", "validation", "verify", "check"],
    "コンペア": ["compare", "match"],
    "一致": ["match", "identical", "equal"],
    "連携": ["integrate", "interface", "link"],
    "終了": ["terminate", "exit", "end", "quit"],
}


CLOSED_RELATIONS = {"realizes", "partial", "related", "member_of"}
# Relations that can carry coverage. `related` is deliberately excluded: it marks topical
# adjacency (and contradiction candidates), never "this BD unit realizes the step".
COVERAGE_RELATIONS = {"realizes", "partial", "member_of"}


def _step_scoring_text(step_dict: dict[str, Any]) -> str:
    """Concatenate a step's textual fields. Every field is nullable in the DB, so each one is
    coalesced explicitly — an f-string would inject the literal token "None" into the scorer."""
    parts = [
        step_dict.get("text_ja") or "",
        step_dict.get("text_en") or "",
        step_dict.get("screen_name_ja") or "",
        step_dict.get("expected_ja") or "",
        step_dict.get("trigger_ja") or "",
    ]
    return " ".join(p for p in parts if p).strip()


def render_bd_candidate_name(unit: BDUnit) -> str:
    """Branch identity as shown to every LLM stage: kind, owning flow, and the edge it guards."""
    if unit.bd_kind == "branch":
        return (
            f"{unit.name} branch of {unit.flow_name}: "
            f"{unit.source_step_name or 'Start'} → {unit.target_step_name or 'End'}"
        )
    return unit.name


def bd_registry_of(ctx: StepAlignContext) -> list[dict[str, Any]]:
    """Serializable record of the candidate catalog shown to the mapper for one step."""
    return [
        {
            "bd_alias": ctx.bd_id_to_alias.get(u.unit_id, u.alias),
            "bd_id": u.unit_id,
            "bd_kind": u.bd_kind,
            "flow_id": u.flow_id,
            "name": render_bd_candidate_name(u),
            "flow_name": u.flow_name,
            "functionality": u.description or "",
        }
        for u in ctx.bd_candidates
    ]


def _ctx_step_text_fields(ctx: StepAlignContext) -> dict[str, Any]:
    return {
        "text_ja": ctx.text_ja,
        "text_en": ctx.text_en,
        "screen_name_ja": ctx.screen_name_ja,
        "expected_ja": ctx.expected_ja,
        "trigger_ja": ctx.trigger_ja,
    }


def _is_non_success_branch(unit: BDUnit) -> bool:
    return unit.bd_kind == "branch" and (unit.name or "").strip().lower() not in ("success", "normal")


def _shares_guard_class(step_dict: dict[str, Any], unit: BDUnit) -> bool:
    """Deterministic corroboration that a user step and a branch guard describe the SAME guard
    class (entity/condition/outcome) rather than merely sharing failure polarity.

    Cross-language semantics are the model's job, so this never rejects a mapping — a negative
    result is recorded as a review flag on the persisted audit. Overlap counts when the step and
    the guard share a bilingual concept, an identifier token, or a screen/program name.
    """
    step_text = _step_scoring_text(step_dict)
    guard_text = f"{unit.description or ''} {unit.name or ''} {unit.flow_name or ''}"
    guard_lower = guard_text.lower()

    for jp_term, en_tokens in BILINGUAL_CONCEPT_MAP.items():
        if jp_term in step_text and any(tok in guard_lower for tok in en_tokens):
            return True

    step_tokens = {t for t in re.findall(r"[A-Za-z0-9_]{3,}", step_text.upper())}
    guard_tokens = {t for t in re.findall(r"[A-Za-z0-9_]{3,}", guard_text.upper())}
    return bool(step_tokens & guard_tokens)


def _shortlist_bd_candidates(
    step_dict: dict[str, Any],
    bd_ctx: BDContext,
    step_code_files: set[str],
) -> list[BDUnit]:
    """Select top plausible BD candidate units for a user step (UP3-2 shortlist overhaul).

    - Threshold: <= 30 returns all units.
    - Cap: top 24 scored units.
    - Mandatory non-SUCCESS branches sit outside the cap.
    - Scorer uses bilingual Japanese -> English concept tokens and code file intersections.
    """
    all_units = bd_ctx.units
    if len(all_units) <= 30:
        return all_units

    step_files_upper = {Path(f).name.upper() for f in step_code_files}
    step_text = _step_scoring_text(step_dict)
    step_text_upper = step_text.upper()

    # Mandatory candidates are decided before scoring and never occupy a scored slot: every
    # non-SUCCESS branch of the step's tier-1-scoped flows ships regardless of lexical score.
    mandatory_branches = [u for u in all_units if _is_non_success_branch(u)]
    mandatory_ids = {u.unit_id for u in mandatory_branches}
    scorable_units = [u for u in all_units if u.unit_id not in mandatory_ids]

    # Extract alphanumeric identifier tokens
    alphanumeric_tokens = set(re.findall(r'[A-Za-z0-9_]{3,}', step_text_upper))
    for sf in step_files_upper:
        stem = Path(sf).stem.upper()
        if len(stem) >= 3:
            alphanumeric_tokens.add(stem)

    scored: list[tuple[int, BDUnit]] = []
    for u in scorable_units:
        score = 0
        u_reason_str = u.verdict_reason if u.verdict_reason else ""
        u_desc = f"{u.name} {u.description or ''} {u_reason_str} {u.flow_name or ''} {u.flow_id}".upper()
        u_desc_lower = u_desc.lower()

        # 1. Primary: shared code file intersection
        for sf in step_files_upper:
            if sf in u_desc:
                score += 12
            sf_stem = Path(sf).stem
            if len(sf_stem) >= 4 and sf_stem in u_desc:
                score += 8

        # 2. Alphanumeric job / program token match
        for token in alphanumeric_tokens:
            if token in u_desc:
                score += 15

        # 3. Bilingual technical concept token overlap
        for jp_term, en_tokens in BILINGUAL_CONCEPT_MAP.items():
            if jp_term in step_text:
                for en_tok in en_tokens:
                    if en_tok.lower() in u_desc_lower:
                        score += 6

        scored.append((score, u))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored_top = [u for _, u in scored[:24]]

    # Combine the full scored cap with every mandatory branch, preserving order and deduping.
    selected_map: dict[str, BDUnit] = {}
    for u in scored_top + mandatory_branches:
        if u.unit_id not in selected_map:
            selected_map[u.unit_id] = u

    return list(selected_map.values())


# ---------------------------------------------------------------------------
# Fused Align Coordinator
# ---------------------------------------------------------------------------

async def align_user_flow_steps(
    db: Any,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
    run_id: str,
    user_steps: list[dict[str, Any]],
    provider_id: str | None,
    local_path: Path,
    step_bd_scope: dict[str, list[str]] | None = None,
) -> tuple[int, int, dict[str, Any]]:
    """Orchestrate Fused Anchor + Mapper alignment across all in-scope user steps.

    `step_bd_scope` (Phase U2 tier-2 rescope, optional): step_id -> bd_flow_ids matched by
    tier-1 activity matching. When provided, the BD catalog loaded and shown to each step is
    shrunk to the union of all scoped flows (query size) and, per step, further restricted to
    that step's own matched flow(s) before shortlisting — falling back to the union catalog for
    any step without its own scope entry. None (the default) reproduces today's unscoped behavior
    byte-for-byte.
    """
    now = utc_now_iso()

    # 1. Load BD Context (union-scoped across all steps' matched flows when step_bd_scope is given)
    if step_bd_scope:
        union_flow_ids: set[str] = set()
        for s in user_steps:
            union_flow_ids.update(step_bd_scope.get(s["id"]) or [])
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id, bd_flow_ids=sorted(union_flow_ids) or None)
    else:
        bd_ctx = await load_bd_context(db, cluster_id, snapshot_id)

    # 2. Manifest files for deterministic snippet retrieval
    async with db.execute(
        "SELECT rel_path FROM manifest_files WHERE snapshot_id = ?",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()
    manifest_paths = {r[0] for r in rows}

    # 3. Seed literal hits across steps & group hits by flow_id to prevent cross-flow leakage
    hits_by_step = await seed_literal_hits(db, snapshot_id, user_steps, local_path)
    hits_by_flow: dict[str, list[SeedHit]] = {}
    for s in user_steps:
        flow_id = s.get("flow_id", "")
        hits_by_flow.setdefault(flow_id, []).extend(hits_by_step.get(s["id"], []))

    # 4. Build step align contexts with deterministic retrieval
    contexts: list[StepAlignContext] = []
    for idx, s in enumerate(user_steps, start=1):
        s_alias = f"u{idx}"
        s_hits = hits_by_step.get(s["id"], [])
        flow_id = s.get("flow_id", "")
        flow_hits = hits_by_flow.get(flow_id, [])

        # Scoped to own flow's hits (no cross-flow leakage)
        screen_files = resolve_step_files(s, s_hits, manifest_paths, flow_hits)
        snippets = await retrieve_step_snippets(db, snapshot_id, s, s_hits, screen_files, local_path)

        snippet_id_map = {f"src{s_i}": snip for s_i, snip in enumerate(snippets, start=1)}
        step_code_files = set(screen_files) | {snip.rel_path for snip in snippets}

        # Per-step tier-2 rescope: restrict to this step's own matched flow(s); fall back to the
        # union catalog (bd_ctx) when the step has no scope entry.
        step_flow_scope = step_bd_scope.get(s["id"]) if step_bd_scope else None
        candidate_ctx = bd_ctx
        if step_flow_scope:
            scoped_units = [u for u in bd_ctx.units if u.flow_id in step_flow_scope]
            if scoped_units:
                candidate_ctx = replace(bd_ctx, units=scoped_units)

        bd_candidates = _shortlist_bd_candidates(s, candidate_ctx, step_code_files)
        bd_alias_map = {f"bd{b_i}": b for b_i, b in enumerate(bd_candidates, start=1)}
        bd_id_to_alias = {b.unit_id: f"bd{b_i}" for b_i, b in enumerate(bd_candidates, start=1)}

        contexts.append(
            StepAlignContext(
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
                in_scope=bool(s.get("in_scope", 1)),
                snippets=snippets,
                snippet_id_map=snippet_id_map,
                bd_candidates=bd_candidates,
                bd_alias_map=bd_alias_map,
                bd_id_to_alias=bd_id_to_alias,
            )
        )

    # 5. Process in batches with concurrency safety (mirrors _verifier.py)
    batch_size = 10
    batches = [contexts[i:i + batch_size] for i in range(0, len(contexts), batch_size)]

    all_anchors_to_insert: list[tuple[str, str, str, str, str, int, int, int, str, str]] = []
    all_mappings_to_insert: list[tuple[str, str, str, str, str, str, float, str, str]] = []
    all_audits_to_insert: list[tuple[str, str, str, str, str, str]] = []

    total_valid_anchors = 0
    total_mappings_saved = 0
    evidence_backed_mappings_count = 0
    mapped_step_ids: set[str] = set()
    evidence_backed_step_ids: set[str] = set()
    incoming_bd_counts: dict[str, int] = {u.unit_id: 0 for u in bd_ctx.units}

    if provider_id is not None:
        semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

        async def _run_batch(batch: list[StepAlignContext]) -> dict[str, Any]:
            async with semaphore:
                results_map, raw_resp, retries, lat, batch_snip_map = await evaluate_align_batch(batch, provider_id)

                batch_anchors = []
                batch_mappings = []
                batch_audits = []
                b_valid_anchors = 0
                b_mappings_count = 0
                b_evidence_backed_count = 0
                b_mapped_steps = set()
                b_ev_backed_steps = set()
                b_bd_counts: dict[str, int] = {}

                for ctx in batch:
                    fused_res = results_map.get(ctx.step_alias, FusedStepResult(step_id=ctx.step_alias))

                    # 1. Deterministic Citation Gate
                    valid_claims: list[dict[str, Any]] = []
                    valid_claim_ids: set[str] = set()

                    for clm in fused_res.claims:
                        snip = batch_snip_map.get(clm.citation.snippet_id) or ctx.snippet_id_map.get(clm.citation.snippet_id)
                        if not snip:
                            continue

                        # Window containment check
                        c_start = clm.citation.line_start
                        c_end = clm.citation.line_end
                        in_window = (snip.line_start <= c_start <= c_end <= snip.line_end)

                        # Citation resolution check
                        resolved = await resolve_citation(db, snapshot_id, snip.rel_path, c_start, c_end)
                        is_valid = in_window and resolved.valid

                        anchor_id = f"uca:{new_id()}"
                        batch_anchors.append((
                            anchor_id,
                            run_id,
                            snapshot_id,
                            ctx.user_step_id,
                            snip.rel_path,
                            c_start,
                            c_end,
                            1 if is_valid else 0,
                            clm.reason,
                            now,
                        ))

                        if is_valid:
                            b_valid_anchors += 1
                            valid_claim_ids.add(clm.claim_id)

                        valid_claims.append({
                            "claim_id": clm.claim_id,
                            "rel_path": snip.rel_path,
                            "line_start": c_start,
                            "line_end": c_end,
                            "valid": is_valid,
                            "in_window": in_window,
                            "reason": clm.reason,
                            "subclaims": clm.subclaims,
                        })

                    # 2. Deterministic BD Mapping Gate
                    valid_mappings: list[dict[str, Any]] = []
                    dropped_mappings: list[dict[str, Any]] = []
                    step_evidence_backed_count = 0

                    for m in fused_res.bd_mappings:
                        target_alias = m.target_alias
                        bd_unit = ctx.bd_alias_map.get(target_alias)
                        if not bd_unit:
                            dropped_mappings.append({
                                "bd_id": target_alias,
                                "relation": m.relation,
                                "reason": m.reason,
                                "error": "MAPPER_UNKNOWN_BD",
                            })
                            continue

                        # Quality Gate: Confidence & relationship validation (UP3-2). `rel` is the
                        # canonical form and it — not the model's spelling — is what gets persisted,
                        # so the stored enum stays closed.
                        rel = (m.relation or "").strip().lower()
                        conf = float(m.confidence or 0.0)
                        has_valid_claim = any(cid in valid_claim_ids for cid in m.support_claim_ids)
                        is_evidence_backed = has_valid_claim and not fused_res.presentation

                        is_quality_valid = True
                        reject_reason = ""

                        if rel not in CLOSED_RELATIONS:
                            is_quality_valid = False
                            reject_reason = f"UNKNOWN_RELATION: {rel}"
                        elif conf < 0.50:
                            is_quality_valid = False
                            reject_reason = f"CONFIDENCE_BELOW_MIN_FLOOR: {conf} < 0.50"
                        elif rel == "realizes" and conf < 0.70:
                            is_quality_valid = False
                            reject_reason = f"REALIZES_BELOW_THRESHOLD: {conf} < 0.70"
                        elif rel == "partial" and conf < 0.60:
                            is_quality_valid = False
                            reject_reason = f"PARTIAL_BELOW_THRESHOLD: {conf} < 0.60"
                        elif rel == "related" and conf < 0.60:
                            is_quality_valid = False
                            reject_reason = f"RELATED_BELOW_THRESHOLD: {conf} < 0.60"
                        elif rel == "member_of":
                            if not _is_non_success_branch(bd_unit):
                                is_quality_valid = False
                                reject_reason = f"MEMBER_OF_FORBIDDEN_ON_TARGET_KIND: {bd_unit.bd_kind}/{bd_unit.name}"
                            elif conf < 0.55:
                                is_quality_valid = False
                                reject_reason = f"MEMBER_OF_BELOW_THRESHOLD: {conf} < 0.55"

                        # Guard-class corroboration: a member_of whose step text shares nothing with
                        # the branch guard is exactly the spray the prompt forbids, but the judgment
                        # is cross-language and semantic — flag it for review, never silently accept.
                        guard_class_flag: str | None = None
                        if is_quality_valid and rel == "member_of" and not _shares_guard_class(_ctx_step_text_fields(ctx), bd_unit):
                            guard_class_flag = "UNCORROBORATED_GUARD_CLASS"

                        if not is_quality_valid:
                            dropped_mappings.append({
                                "bd_id": bd_unit.unit_id,
                                "bd_alias": target_alias,
                                "relation": m.relation,
                                "confidence": conf,
                                "reason": m.reason,
                                "error": "BELOW_QUALITY_THRESHOLD",
                                "reject_reason": reject_reason,
                            })
                            continue

                        if is_evidence_backed:
                            step_evidence_backed_count += 1
                            b_evidence_backed_count += 1
                            b_ev_backed_steps.add(ctx.user_step_id)

                        mapping_id = f"ubm:{new_id()}"
                        batch_mappings.append((
                            mapping_id,
                            run_id,
                            ctx.user_step_id,
                            bd_unit.bd_kind,
                            bd_unit.unit_id,
                            rel,
                            conf,
                            m.reason,
                            now,
                        ))

                        b_mappings_count += 1
                        b_mapped_steps.add(ctx.user_step_id)
                        b_bd_counts[bd_unit.unit_id] = b_bd_counts.get(bd_unit.unit_id, 0) + 1

                        valid_mappings.append({
                            "bd_id": bd_unit.unit_id,
                            "bd_alias": target_alias,
                            "bd_kind": bd_unit.bd_kind,
                            "relation": rel,
                            "confidence": conf,
                            "reason": m.reason,
                            "support_claim_ids": m.support_claim_ids,
                            "evidence_backed": is_evidence_backed,
                            "guard_class_flag": guard_class_flag,
                        })

                    # 3. Store Step Audit Artifact
                    audit_payload = {
                        "user_step_id": ctx.user_step_id,
                        "step_alias": ctx.step_alias,
                        "presentation": fused_res.presentation,
                        "status": "UNVERIFIABLE" if fused_res.presentation else ("CONFIRMED" if step_evidence_backed_count > 0 else "UNCONFIRMED"),
                        "claims": valid_claims,
                        "mappings": valid_mappings,
                        "dropped_mappings": dropped_mappings,
                        "dropped_unknown_bd_count": len(dropped_mappings),
                        "evidence_backed_mappings_count": step_evidence_backed_count,
                        # The EXACT candidate registry this step's mapper saw. The verifier reloads
                        # it verbatim instead of re-deriving a shortlist, so both stages judge the
                        # same catalog under the same aliases (a prerequisite for safe BD_MISSING).
                        "bd_registry": bd_registry_of(ctx),
                        "raw_response": raw_resp,
                        "retry_count": retries,
                        "latency_ms": lat,
                    }

                    batch_audits.append((
                        f"ura:{new_id()}",
                        run_id,
                        doc_id,
                        f"align:{ctx.user_step_id}",
                        json.dumps(audit_payload, ensure_ascii=False),
                        now,
                    ))

                return {
                    "anchors": batch_anchors,
                    "mappings": batch_mappings,
                    "audits": batch_audits,
                    "valid_anchors": b_valid_anchors,
                    "mappings_count": b_mappings_count,
                    "evidence_backed_count": b_evidence_backed_count,
                    "mapped_steps": b_mapped_steps,
                    "ev_backed_steps": b_ev_backed_steps,
                    "bd_counts": b_bd_counts,
                    "retries": retries,
                }

        # Concurrency execution with return_exceptions=True
        batch_results = await asyncio.gather(*[_run_batch(b) for b in batches], return_exceptions=True)

        align_physical_retries = 0
        for res in batch_results:
            if isinstance(res, Exception):
                logger.error(f"Align batch execution failed: {res}")
                continue
            if isinstance(res, dict):
                align_physical_retries += res.get("retries", 0)
                all_anchors_to_insert.extend(res["anchors"])
                all_mappings_to_insert.extend(res["mappings"])
                all_audits_to_insert.extend(res["audits"])
                total_valid_anchors += res["valid_anchors"]
                total_mappings_saved += res["mappings_count"]
                evidence_backed_mappings_count += res["evidence_backed_count"]
                mapped_step_ids.update(res["mapped_steps"])
                evidence_backed_step_ids.update(res["ev_backed_steps"])
                for u_id, cnt in res["bd_counts"].items():
                    incoming_bd_counts[u_id] = incoming_bd_counts.get(u_id, 0) + cnt

        # Execute all DB writes sequentially
        for anchor in all_anchors_to_insert:
            await db.execute(
                "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'llm_matched', ?, ?, ?)",
                anchor,
            )

        for mapping in all_mappings_to_insert:
            await db.execute(
                "INSERT INTO user_bd_mappings (id, run_id, user_step_id, bd_kind, bd_id, relation, confidence, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                mapping,
            )

        for audit in all_audits_to_insert:
            await db.execute(
                "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                audit,
            )

    else:
        # Offline mode (provider_id is None)
        for ctx in contexts:
            audit_payload = {
                "user_step_id": ctx.user_step_id,
                "step_alias": ctx.step_alias,
                "presentation": False,
                "status": "OFFLINE",
                "claims": [],
                "mappings": [],
                "dropped_mappings": [],
                "dropped_unknown_bd_count": 0,
                "evidence_backed_mappings_count": 0,
                "offline": True,
            }
            await db.execute(
                "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"ura:{new_id()}",
                    run_id,
                    doc_id,
                    f"align:{ctx.user_step_id}",
                    json.dumps(audit_payload, ensure_ascii=False),
                    now,
                ),
            )

    # 6. Reverse Index & Run Summary Artifact
    unmapped_user_steps = [s["id"] for s in user_steps if s["id"] not in mapped_step_ids]
    unmapped_bd_units = [u_id for u_id, count in incoming_bd_counts.items() if count == 0]

    bd_unit_counts_summary = [
        {
            "bd_id": u.unit_id,
            "bd_kind": u.bd_kind,
            "name": u.name,
            "flow_name": u.name if u.bd_kind == "flow" else "",
            "incoming_count": incoming_bd_counts.get(u.unit_id, 0),
            "is_bd_extra_candidate": incoming_bd_counts.get(u.unit_id, 0) == 0,
        }
        for u in bd_ctx.units
    ]

    run_summary = {
        "stage": "fused_user_flow_alignment",
        "run_id": run_id,
        "doc_id": doc_id,
        "cluster_id": cluster_id,
        "snapshot_id": snapshot_id,
        "total_user_steps": len(user_steps),
        "total_bd_units": len(bd_ctx.units),
        "total_valid_anchors": total_valid_anchors,
        "total_mappings": total_mappings_saved,
        "evidence_backed_mappings_count": evidence_backed_mappings_count,
        "mapped_user_step_count": len(mapped_step_ids),
        "evidence_backed_mapped_step_count": len(evidence_backed_step_ids),
        # UP3-2 metrics split: semantic mapping recall is reported separately from code-evidence recall.
        "semantic_mapped_steps": len(mapped_step_ids),
        "evidence_backed_steps": len(evidence_backed_step_ids),
        "unmapped_user_step_ids": unmapped_user_steps,
        "unmapped_bd_unit_ids": unmapped_bd_units,
        "bd_unit_counts": bd_unit_counts_summary,
        # Real align-stage LLM cost (logical batches + physical provider attempts incl. retries)
        "align_logical_batches": len(batches),
        "align_physical_attempts": len(batches) + (align_physical_retries if provider_id is not None else 0),
    }

    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES (?, ?, ?, '__run_summary__', ?, ?)",
        (
            f"ura:{new_id()}",
            run_id,
            doc_id,
            json.dumps(run_summary, ensure_ascii=False),
            now,
        ),
    )

    await db.commit()
    return total_valid_anchors, len(mapped_step_ids), run_summary
