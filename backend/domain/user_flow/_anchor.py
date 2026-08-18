"""Stage U2: User-step to Code Evidence Anchoring.

- Seed literal matching (deterministic PRIOR, NFKC normalized, token filtering, screen prefix variants).
- Screen resolution + snippet retrieval (PRIMARY literal-line window cutting for ISPF panels + SECONDARY occurrence windows for COBOL/CLIST programs + flow-level fallback).
- LLM#2 Matcher: evaluates snippets against user steps, with citation gating (resolve_citation + window containment).
- Audit artifact recording in user_run_artifacts and user_code_anchors.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
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
from domain.business_flow_integrity._retrieval import Snippet, expand_unit_snippets
from domain.doc_graph._llm_citation import _parse_llm_json
from domain.model_connector.types import ChatMessage, ChatRequest
from shared.logger import logger
from shared.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeedHit:
    rel_path: str
    line: int
    matched_needle: str


class AnchorCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rel_path: str
    line_start: int
    line_end: int


class AnchorStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    matched: bool
    citations: list[AnchorCitation] = Field(default_factory=list)
    subclaims: list[str] = Field(default_factory=list)
    reason: str


class AnchorBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[AnchorStepResult]


# ---------------------------------------------------------------------------
# 1. Seed Literal Matcher
# ---------------------------------------------------------------------------

_ASSET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{3,9}$")
_CJK_OR_KANA_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uff66-\uff9f]")
_PUNCT_SPLIT_RE = re.compile(
    r"[「」『』（）()【】\[\]。、,./：:；;＝=＜＞<>*%!　\s\-_+&|#@\^~\\`\'\"]+"
)


def _get_prefix_variants(token: str) -> set[str]:
    """Generate logical-screen prefix variants for an asset id (SPEC §4).
    e.g. HNIXLOT ⇄ FHNIXLOT ⇄ PHNIXLOT.
    """
    variants = {token}
    upper = token.upper()
    if upper.startswith("F") or upper.startswith("P"):
        base = upper[1:]
        if len(base) >= 4:
            variants.add(base)
            variants.add(f"F{base}")
            variants.add(f"P{base}")
    else:
        variants.add(f"F{upper}")
        variants.add(f"P{upper}")
    return variants


def _extract_step_needles(step: dict[str, Any]) -> set[str]:
    """Extract NFKC-normalized search tokens from a step."""
    fields_to_combine = [
        step.get("text_ja") or "",
        step.get("expected_ja") or "",
        step.get("screen_name_ja") or "",
        step.get("trigger_ja") or "",
    ]
    raw_combined = " ".join(fields_to_combine)
    norm_text = unicodedata.normalize("NFKC", raw_combined)

    tokens = _PUNCT_SPLIT_RE.split(norm_text)
    needles: set[str] = set()

    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        # Check if CJK/kana >= 2 chars
        if len(t) >= 2 and _CJK_OR_KANA_RE.search(t):
            needles.add(t)
        # Check if asset-id shape
        elif _ASSET_ID_RE.match(t):
            variants = _get_prefix_variants(t)
            needles.update(variants)

    return needles


async def seed_literal_hits(
    db: Any,
    snapshot_id: str,
    steps: list[dict[str, Any]],
    local_path: str | Path,
) -> dict[str, list[SeedHit]]:
    """Scan all manifest files for NFKC-normalized needle substrings (deterministic PRIOR)."""
    # Load manifest files
    async with db.execute(
        "SELECT rel_path FROM manifest_files WHERE snapshot_id = ? ORDER BY rel_path",
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()

    manifest_paths = [r[0] if isinstance(r, (tuple, list)) else r["rel_path"] for r in rows]
    if not manifest_paths and local_path and str(local_path).strip() and Path(local_path).is_dir():
        # Fallback: scan files directly on disk under local_path
        manifest_paths = [
            str(p.relative_to(local_path)).replace("\\", "/")
            for p in Path(local_path).rglob("*")
            if p.is_file() and not p.name.startswith(".")
        ]

    # Pre-extract needles for all steps
    step_needles: dict[str, set[str]] = {
        s["id"]: _extract_step_needles(s) for s in steps
    }

    hits_by_step: dict[str, list[SeedHit]] = {s["id"]: [] for s in steps}

    # Read each manifest file once
    for rel_path in manifest_paths:
        fpath = Path(local_path) / rel_path
        if not fpath.is_file():
            continue
        try:
            raw_bytes = fpath.read_bytes()
        except Exception:
            continue

        raw_lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
        norm_lines = [unicodedata.normalize("NFKC", l) for l in raw_lines]

        # Scan for each step's needles
        for step_id, needles in step_needles.items():
            if not needles:
                continue
            curr_step_hits = hits_by_step[step_id]
            if len(curr_step_hits) >= 25:
                continue

            file_hits = 0
            for line_idx, line_nfkc in enumerate(norm_lines, start=1):
                if file_hits >= 8 or len(curr_step_hits) >= 25:
                    break
                for needle in needles:
                    if needle in line_nfkc:
                        hit = SeedHit(rel_path=rel_path, line=line_idx, matched_needle=needle)
                        curr_step_hits.append(hit)
                        file_hits += 1
                        break  # move to next line

    return hits_by_step


# ---------------------------------------------------------------------------
# 2. Screen Resolution & Snippet Retrieval (Primary literal-cut + Secondary occurrence)
# ---------------------------------------------------------------------------

def cut_line_window(
    local_path: str | Path,
    rel_path: str,
    line: int,
    pad: int = 10,
    max_lines: int = 80,
    max_chars: int = 3000,
) -> Snippet | None:
    """Cut a verbatim line window from a local file without requiring evidence occurrences."""
    fpath = Path(local_path) / rel_path
    if not fpath.is_file():
        return None
    try:
        raw_bytes = fpath.read_bytes()
    except Exception:
        return None

    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return None

    line_start = max(1, line - pad)
    line_end = min(total_lines, line + pad)

    if line_end - line_start + 1 > max_lines:
        line_end = line_start + max_lines - 1

    cut_lines = lines[line_start - 1 : line_end]
    # Enforce the char budget by WHOLE lines so line_end stays honest — the window-containment
    # gate trusts these bounds, so text must not claim lines it does not actually contain.
    if sum(len(cl) + 1 for cl in cut_lines) > max_chars:
        kept: list[str] = []
        total = 0
        for cl in cut_lines:
            if kept and total + len(cl) + 1 > max_chars:
                break
            kept.append(cl)
            total += len(cl) + 1
        cut_lines = kept
        line_end = line_start + len(cut_lines) - 1
    cut_text = "\n".join(cut_lines)

    return Snippet(
        rel_path=rel_path,
        line_start=line_start,
        line_end=line_end,
        text=cut_text,
        source_sha256=source_sha256,
        occurrence_id=f"literal:{rel_path}:{line}",
        parse_status="ok",
    )


def _merge_overlapping_snippets(
    snippets: list[Snippet],
    local_path: str | Path,
    max_lines: int = 80,
    max_chars: int = 3000,
) -> list[Snippet]:
    """Merge overlapping or adjacent snippet windows for the same file."""
    by_file: dict[str, list[Snippet]] = {}
    for s in snippets:
        by_file.setdefault(s.rel_path, []).append(s)

    merged: list[Snippet] = []
    for rel_path, file_snippets in by_file.items():
        if len(file_snippets) == 1:
            merged.append(file_snippets[0])
            continue

        sorted_snips = sorted(file_snippets, key=lambda s: s.line_start)
        clusters: list[list[Snippet]] = []
        for s in sorted_snips:
            if not clusters:
                clusters.append([s])
                continue
            last_cluster = clusters[-1]
            last_max_end = max(item.line_end for item in last_cluster)
            # Merge if overlapping or within 3 lines
            if s.line_start <= last_max_end + 3:
                last_cluster.append(s)
            else:
                clusters.append([s])

        for cl in clusters:
            if len(cl) == 1:
                merged.append(cl[0])
                continue
            c_start = min(s.line_start for s in cl)
            c_end = max(s.line_end for s in cl)
            if c_end - c_start + 1 > max_lines:
                c_end = c_start + max_lines - 1

            fpath = Path(local_path) / rel_path
            try:
                raw_bytes = fpath.read_bytes()
                lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
                window = lines[c_start - 1 : c_end]
                # Trim by whole lines to keep c_end honest (the gate trusts these bounds).
                # NOTE: use `ln` (not `cl`) — `cl` is the outer cluster loop var; shadowing it
                # corrupts the Snippet built below.
                if sum(len(ln) + 1 for ln in window) > max_chars:
                    kept: list[str] = []
                    total = 0
                    for ln in window:
                        if kept and total + len(ln) + 1 > max_chars:
                            break
                        kept.append(ln)
                        total += len(ln) + 1
                    window = kept
                    c_end = c_start + len(window) - 1
                cut_text = "\n".join(window)
                merged.append(
                    Snippet(
                        rel_path=rel_path,
                        line_start=c_start,
                        line_end=c_end,
                        text=cut_text,
                        source_sha256=cl[0].source_sha256,
                        occurrence_id=cl[0].occurrence_id,
                        parse_status="ok",
                    )
                )
            except Exception:
                merged.append(cl[0])

    return merged


def resolve_step_files(
    step: dict[str, Any],
    seed_hits: list[SeedHit],
    manifest_paths: set[str],
    flow_step_hits: list[SeedHit],
) -> set[str]:
    """Resolve relevant screen/program family files for a step (SPEC §4)."""
    resolved: set[str] = set()

    # 1. Files hit by this step's seeds
    for h in seed_hits:
        resolved.add(h.rel_path)

    # 2. Screen program family bridge: for every asset id or hit file, add prefix-variant sibling files
    asset_tokens: set[str] = set()
    for tok in _PUNCT_SPLIT_RE.split(unicodedata.normalize("NFKC", step.get("text_ja") or "")):
        if _ASSET_ID_RE.match(tok):
            asset_tokens.update(_get_prefix_variants(tok))
    for h in seed_hits:
        p_stem = Path(h.rel_path).stem
        if _ASSET_ID_RE.match(p_stem):
            asset_tokens.update(_get_prefix_variants(p_stem))

    for m in manifest_paths:
        m_stem = Path(m).stem.upper()
        if m_stem in asset_tokens:
            resolved.add(m)

    # 3. Flow-level fallback: if step had no seeds, inherit files from all steps in the same flow
    if not resolved and flow_step_hits:
        for fh in flow_step_hits:
            resolved.add(fh.rel_path)
            f_stem = Path(fh.rel_path).stem
            if _ASSET_ID_RE.match(f_stem):
                for fvar in _get_prefix_variants(f_stem):
                    for m in manifest_paths:
                        if Path(m).stem.upper() == fvar:
                            resolved.add(m)

    return resolved


async def retrieve_step_snippets(
    db: Any,
    snapshot_id: str,
    step: dict[str, Any],
    seed_hits: list[SeedHit],
    screen_files: set[str],
    local_path: str | Path,
) -> list[Snippet]:
    """Retrieve primary literal-cut windows (for panels) and secondary occurrence windows (for programs)."""
    # PRIMARY: literal-cut windows for seed hits (mostly panels), then merge overlaps.
    primary: list[Snippet] = []
    for h in seed_hits:
        snip = cut_line_window(local_path, h.rel_path, h.line, pad=10, max_lines=80)
        if snip:
            primary.append(snip)
    primary = _merge_overlapping_snippets(primary, local_path)

    # SECONDARY: occurrence windows from program files (COBOL, CLIST) — semantically indexed guards.
    secondary: list[Snippet] = []
    program_files = [
        f for f in screen_files
        if f.lower().endswith((".cbl", ".cob", ".clist", ".pco", ".sqb"))
    ]
    if program_files:
        try:
            ret_res = await expand_unit_snippets(
                db,
                snapshot_id,
                {"unit_id": step["id"], "unit_kind": "step"},
                rel_paths=program_files,
            )
            for occ_snip in ret_res.snippets:
                if not any(
                    s.rel_path == occ_snip.rel_path
                    and max(s.line_start, occ_snip.line_start) <= min(s.line_end, occ_snip.line_end)
                    for s in primary
                ):
                    secondary.append(occ_snip)
        except Exception as e:
            logger.debug(f"Secondary occurrence retrieval skipped: {e}")

    # Order occurrence-indexed guards BEFORE raw literal windows so, under the cap, the gold
    # logic evidence for a validation step is never crowded out by broad/noisy seed hits.
    snippets = secondary + primary

    # Dedupe by rel_path + line range
    deduped: list[Snippet] = []
    seen = set()
    for s in snippets:
        key = (s.rel_path, s.line_start, s.line_end)
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    # Cap <= 6 snippets and <= 8000 total characters
    final_snippets: list[Snippet] = []
    total_chars = 0
    for s in deduped:
        if len(final_snippets) >= 6:
            break
        if total_chars + len(s.text) > 8000 and final_snippets:
            break
        final_snippets.append(s)
        total_chars += len(s.text)

    return final_snippets


# ---------------------------------------------------------------------------
# 3. LLM#2 Matcher & Gating
# ---------------------------------------------------------------------------

_MATCHER_SYSTEM_PROMPT = """You are an expert legacy software modernization analyst.
Your task is to evaluate whether specific lines in legacy source files (ISPF panel definitions, COBOL programs, CLIST scripts) realize, demonstrate, or contradict the user action or expectation described in a Japanese user flow step.

Instructions:
1. For each user step, examine the provided source snippets and seed hints:
   - Japanese display literals in an ISPF panel (e.g. `条件区分`, PF-key action labels like `中止/クリア/更新`, option numbers like `1.ｺｲﾙ 2.ﾒｯｷ`) are direct evidence for user-visible step claims.
   - Guard statements, validation logic, IF checks, routing/dispatching, and message outputs in COBOL/CLIST programs are direct evidence for validation/business logic steps.
2. Citations:
   - Cite the exact `rel_path`, `line_start`, and `line_end` (1-based inclusive) inside the shown snippets that realize the step behavior.
   - Citations MUST fall within the line ranges of the provided snippets.
3. Matching verdict:
   - If the code realizes the step: set `matched: true`, provide citations, list subclaims, and explain in `reason`.
   - If the code CONTRADICTS the step (e.g. a different key mapping, a contradictory message): still provide citations, set `matched: false`, and state "contradicting" in `reason`.
   - If no relevant code is present: set `matched: false`, `citations: []`, and state why in `reason`.
   - Presentation-only claims (e.g. font size, layout geometry, colors, pixel positions): set `matched: false`, `citations: []`, and start `reason` with `PRESENTATION`.
4. The `seed_hints` are UNVERIFIED deterministic string matches — treat them only as pointers to inspect, and judge from the snippet text itself, never from a seed hint alone.
5. Respond ONLY in English (Japanese may appear verbatim inside `subclaims` when quoting a panel literal). Every unit_id from the input MUST appear exactly once.

Output Format:
You MUST respond with a valid JSON object matching this schema:
{
  "results": [
    {
      "unit_id": "u1",
      "matched": true,
      "citations": [
        {
          "rel_path": "FAKESCR.ipf",
          "line_start": 11,
          "line_end": 12
        }
      ],
      "subclaims": ["条件区分 option set in panel"],
      "reason": "Option 1.ｺｲﾙ matches step coil testing input."
    }
  ]
}
Every unit_id from the input MUST appear exactly once in the results list.
"""


async def evaluate_anchor_batch(
    batch_steps: list[tuple[dict[str, Any], list[Snippet], list[SeedHit]]],
    provider_id: str,
) -> tuple[dict[str, AnchorStepResult], str, int, float, bool, str | None]:
    """Run LLM#2 Matcher on a batch of steps (<=5 steps)."""
    step_alias_map: dict[str, str] = {}
    prompt_steps: list[dict[str, Any]] = []

    for idx, (step, snippets, seed_hits) in enumerate(batch_steps, start=1):
        u_alias = f"u{idx}"
        step_alias_map[u_alias] = step["id"]

        snip_dicts = [
            {
                "rel_path": s.rel_path,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "text": s.text,
            }
            for s in snippets
        ]
        seed_hint_dicts = [
            {"rel_path": h.rel_path, "line": h.line, "needle": h.matched_needle}
            for h in seed_hits
        ]

        prompt_steps.append({
            "unit_id": u_alias,
            "kind": step.get("kind"),
            "section_id": step.get("section_id"),
            "text_ja": step.get("text_ja"),
            "text_en": step.get("text_en"),
            "trigger_ja": step.get("trigger_ja"),
            "expected_ja": step.get("expected_ja"),
            "screen_name_ja": step.get("screen_name_ja"),
            "snippets": snip_dicts,
            "seed_hints": seed_hint_dicts,
        })

    expected_aliases = set(step_alias_map.keys())
    attempts = {"n": 0}

    def _build_req(effort: str) -> ChatRequest:
        attempts["n"] += 1
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_MATCHER_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=json.dumps({"steps_to_evaluate": prompt_steps}, ensure_ascii=False, indent=2),
                ),
            ],
            stream=True,
            max_completion_tokens=50000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse(text: str) -> dict[str, AnchorStepResult]:
        parsed = coerce_results_wrapper(_parse_llm_json(text))
        validated = AnchorBatchResponse.model_validate(parsed)
        assert_exact_id_coverage([r.unit_id for r in validated.results], expected_aliases, "AnchorMatcher")
        return {
            step_alias_map[r.unit_id]: r
            for r in validated.results
        }

    start = time.monotonic()
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse, first_effort="low", label="UserFlow Anchor Matcher (LLM#2)",
        provider_id=provider_id,
    )
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    retry_count = max(attempts["n"] - 1, 0)

    if ladder_res is None:
        fallback_results = {
            step["id"]: AnchorStepResult(
                unit_id=u_alias,
                matched=False,
                citations=[],
                subclaims=[],
                reason="LADDER_FAILED",
            )
            for u_alias, step in [(f"u{i}", s[0]) for i, s in enumerate(batch_steps, 1)]
        }
        return fallback_results, "", retry_count, latency_ms, False, "LADDER_FAILED"

    res_dict, raw_text = ladder_res
    return res_dict, raw_text, retry_count, latency_ms, True, None


# ---------------------------------------------------------------------------
# 4. End-to-End Anchoring Coordinator for Stage U2
# ---------------------------------------------------------------------------

async def anchor_user_steps(
    db: Any,
    doc_id: str,
    snapshot_id: str,
    run_id: str,
    in_scope_steps: list[dict[str, Any]],
    provider_id: str | None,
    local_path: str | Path,
) -> tuple[int, int]:
    """Execute Stage U2 evidence anchoring for all in-scope user steps.
    Returns (anchored_steps_count, llm_batches_issued).
    """
    now = utc_now_iso()

    # 1. Deterministic Seed Literal Hits
    hits_by_step = await seed_literal_hits(db, snapshot_id, in_scope_steps, local_path)

    # 2. Screen Resolution & Snippet Retrieval per step
    async with db.execute("SELECT rel_path FROM manifest_files WHERE snapshot_id = ?", (snapshot_id,)) as cur:
        mf_rows = await cur.fetchall()
    manifest_paths = {r[0] if isinstance(r, (tuple, list)) else r["rel_path"] for r in mf_rows}

    # Group hits by flow_id for flow fallback
    flow_step_hits: dict[str, list[SeedHit]] = {}
    for s in in_scope_steps:
        f_id = s.get("flow_id", "")
        flow_step_hits.setdefault(f_id, []).extend(hits_by_step.get(s["id"], []))

    step_snippets: dict[str, list[Snippet]] = {}
    for s in in_scope_steps:
        s_id = s["id"]
        f_id = s.get("flow_id", "")
        s_hits = hits_by_step.get(s_id, [])
        f_hits = flow_step_hits.get(f_id, [])
        screen_files = resolve_step_files(s, s_hits, manifest_paths, f_hits)
        snips = await retrieve_step_snippets(db, snapshot_id, s, s_hits, screen_files, local_path)
        step_snippets[s_id] = snips

    if provider_id is None:
        # Offline mode: record seeds/retrieval audit but no LLM matching
        for s in in_scope_steps:
            s_id = s["id"]
            snips = step_snippets.get(s_id, [])
            hits = hits_by_step.get(s_id, [])
            audit_payload = {
                "step_id": s_id,
                "offline": True,
                "seed_hits": [{"rel_path": h.rel_path, "line": h.line, "needle": h.matched_needle} for h in hits],
                "snippets_count": len(snips),
                "snippets": [
                    {"rel_path": sn.rel_path, "line_start": sn.line_start, "line_end": sn.line_end}
                    for sn in snips
                ],
            }
            await db.execute(
                "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"ufart:{new_id()}",
                    run_id,
                    doc_id,
                    f"anchor:{s_id}",
                    json.dumps(audit_payload, ensure_ascii=False),
                    now,
                ),
            )
        return 0, 0

    # 3. LLM#2 Matcher Batches (<=5 steps per batch)
    batches: list[list[tuple[dict[str, Any], list[Snippet], list[SeedHit]]]] = []
    curr_batch: list[tuple[dict[str, Any], list[Snippet], list[SeedHit]]] = []

    curr_chars = 0
    for s in in_scope_steps:
        s_id = s["id"]
        s_snips = step_snippets.get(s_id, [])
        s_chars = sum(len(sn.text) for sn in s_snips)
        # Cap batch by count (<=5) AND total snippet chars (<=20000) so a batch of large windows
        # cannot overflow the model context alongside the completion budget.
        if curr_batch and (len(curr_batch) >= 5 or curr_chars + s_chars > 20000):
            batches.append(curr_batch)
            curr_batch = []
            curr_chars = 0
        curr_batch.append((s, s_snips, hits_by_step.get(s_id, [])))
        curr_chars += s_chars
    if curr_batch:
        batches.append(curr_batch)

    anchored_step_ids: set[str] = set()
    batches_issued = len(batches)
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _process_batch(b: list[tuple[dict[str, Any], list[Snippet], list[SeedHit]]]):
        async with semaphore:
            res_dict, raw_text, retries, lat, ok, err = await evaluate_anchor_batch(b, provider_id)

            for step, snippets, seed_hits in b:
                s_id = step["id"]
                step_res = res_dict.get(s_id)
                resolved_citations_list = []
                has_valid_anchor = False

                if step_res and step_res.citations:
                    for cit in step_res.citations:
                        # 1. Verify location against file bytes
                        res_cit = await resolve_citation(db, snapshot_id, cit.rel_path, cit.line_start, cit.line_end)

                        # 2. Verify window containment: citation must be within at least one shown snippet
                        in_window = any(
                            sn.rel_path == cit.rel_path
                            and sn.line_start <= cit.line_start
                            and cit.line_end <= sn.line_end
                            for sn in snippets
                        )

                        if not in_window:
                            is_valid = 0
                            reject_reason = "CITATION_OUT_OF_WINDOW"
                        elif not res_cit.valid:
                            is_valid = 0
                            reject_reason = res_cit.reject_reason
                        else:
                            is_valid = 1
                            reject_reason = None
                            has_valid_anchor = True

                        resolved_citations_list.append({
                            "rel_path": cit.rel_path,
                            "line_start": cit.line_start,
                            "line_end": cit.line_end,
                            "valid": is_valid,
                            "reject_reason": reject_reason,
                            "fetched_text": res_cit.fetched_text,
                        })

                        # Insert LLM matched anchor
                        await db.execute(
                            "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                f"uca:{new_id()}",
                                run_id,
                                snapshot_id,
                                s_id,
                                cit.rel_path,
                                cit.line_start,
                                cit.line_end,
                                "llm_matched",
                                is_valid,
                                reject_reason,
                                now,
                            ),
                        )

                # Record seed literal anchors if matched
                if has_valid_anchor:
                    anchored_step_ids.add(s_id)
                    for h in seed_hits:
                        await db.execute(
                            "INSERT INTO user_code_anchors (id, run_id, snapshot_id, step_id, rel_path, line_start, line_end, kind, valid, reason, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                f"uca:{new_id()}",
                                run_id,
                                snapshot_id,
                                s_id,
                                h.rel_path,
                                h.line,
                                h.line,
                                "seed_literal",
                                1,
                                None,
                                now,
                            ),
                        )

                # Insert full audit artifact
                audit_payload = {
                    "step_id": s_id,
                    "matched": step_res.matched if step_res else False,
                    "reason": step_res.reason if step_res else (err or "NO_RESPONSE"),
                    "subclaims": step_res.subclaims if step_res else [],
                    "citations": resolved_citations_list,
                    "seed_hits": [{"rel_path": h.rel_path, "line": h.line, "needle": h.matched_needle} for h in seed_hits],
                    "snippets": [
                        {"rel_path": sn.rel_path, "line_start": sn.line_start, "line_end": sn.line_end}
                        for sn in snippets
                    ],
                    "agent_raw": raw_text,
                    "retry_count": retries,
                    "latency_ms": lat,
                    "success": ok,
                }
                await db.execute(
                    "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"ufart:{new_id()}",
                        run_id,
                        doc_id,
                        f"anchor:{s_id}",
                        json.dumps(audit_payload, ensure_ascii=False),
                        now,
                    ),
                )

    await asyncio.gather(*[_process_batch(b) for b in batches])

    return len(anchored_step_ids), batches_issued
