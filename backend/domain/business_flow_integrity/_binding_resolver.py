"""Double-agent LLM binding resolver for residual BD units (Ticket P5-3-FIX)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from shared.logger import logger

from ..doc_graph._llm_citation import _parse_llm_json
from ..model_connector.types import ChatMessage, ChatRequest
from ._llm import LLM_CONCURRENCY, assert_exact_id_coverage, call_with_reasoning_ladder, coerce_results_wrapper

_BATCH_SIZE = 5


class LLMExtractorItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    candidates: list[str]
    no_file: bool
    reason: str


class LLMExtractorBatchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMExtractorItem]


class LLMVerifierItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    confirmed_rel_paths: list[str]
    no_file: bool
    reason: str


class LLMVerifierBatchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMVerifierItem]


@dataclass(frozen=True)
class BindingResolutionResult:
    unit_id: str
    confirmed_rel_paths: list[str]  # Sub-set of manifest rel_paths
    no_file: bool
    reason: str
    agent1_raw: dict[str, Any] | None
    agent2_raw: dict[str, Any] | None
    retry_count: int


_EXTRACTOR_SYSTEM_PROMPT = """You are a main-frame codebase binding extractor. Your task is to examine residual business flow units \
(BD step or branch prose + label + binding string) and propose candidate manifest source files that realize them.

Rules:
1. You are given a closed list of manifest source files with role hints (in this system prompt below).
2. candidates MUST be a strict subset of the given manifest rel_path strings. NEVER invent or hallucinate filenames not in the manifest.
3. If the unit describes a screen, menu, or panel and a .pfd or .ipf file of that name IS in the manifest, that panel \
file itself is the backing file — put it in candidates. Do NOT set no_file merely because no program "loads" the panel.
4. Only set no_file to true when the unit describes an EXTERNAL system, a dataset/library name, or an abstract \
capability with genuinely no backing file in the manifest. Give a clear reason.
5. Output ONLY a single JSON object with EXACTLY this top-level shape — no wrapper object, no extra keys:
{
  "results": [
    {
      "unit_id": "bs:example-1",
      "candidates": ["HNIXLOT.cbl"],
      "no_file": false,
      "reason": "Step label explicitly names HNIXLOT which matches the COBOL program file."
    }
  ]
}
"""

_VERIFIER_SYSTEM_PROMPT = """You are an independent manifest-anchored binding verifier. Your task is to verify proposed file candidates \
for residual business flow units.

Rules:
1. You are given the raw BD unit (label + prose + binding string) and Agent 1's proposed candidates/no_file judgment. The \
closed manifest source file list is in this system prompt below.
2. Evaluate independently: check if the proposed manifest candidates genuinely match the unit's described functionality or program name.
3. confirmed_rel_paths MUST be a strict subset of the manifest rel_path strings. NEVER invent or hallucinate filenames not in the manifest.
4. If the unit describes a screen, menu, or panel and a .pfd or .ipf file of that name IS in the manifest, that panel file \
itself is the backing file — confirm it. Do NOT set no_file merely because no program "loads" the panel.
5. Set no_file to true (and confirmed_rel_paths to []) only when you find no candidate file actually realizes the unit — \
i.e. it is a genuinely external system, dataset/library, or abstract capability.
6. Output ONLY a single JSON object with EXACTLY this top-level shape — no wrapper object, no extra keys:
{
  "results": [
    {
      "unit_id": "bs:example-1",
      "confirmed_rel_paths": ["HNIXLOT.cbl"],
      "no_file": false,
      "reason": "Confirmed HNIXLOT.cbl is the backing COBOL program for this step."
    }
  ]
}
"""


def _role_hint_for_rel_path(rel_path: str) -> str:
    lower = rel_path.lower()
    if lower.endswith((".cbl", ".cob")):
        return "COBOL source program"
    if lower.endswith((".jcl", ".prc")):
        return "JCL job / step procedure"
    if lower.endswith(".clist"):
        return "TSO CLIST procedure"
    if lower.endswith(".pfd"):
        return "ISPF menu panel"
    if lower.endswith(".ipf"):
        return "ISPF screen panel"
    return "source file"


async def _evaluate_extractor_batch(
    batch_units: list[dict[str, Any]],
    system_prompt: str,
    provider_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, LLMExtractorItem], int]:
    async with semaphore:
        expected_ids = {u["unit_id"] for u in batch_units}
        # Only the variable per-batch units go in the user message; the invariant manifest lives in
        # the system prompt so the provider can cache-hit the shared prefix across all batches.
        prompt_payload = {
            "units": [
                {
                    "unit_id": u["unit_id"],
                    "unit_kind": u["unit_kind"],
                    "prose": u["prose"],
                    "label": u.get("label"),
                    "binding": u.get("binding"),
                    "binding_type": u.get("binding_type"),
                }
                for u in batch_units
            ],
        }

        attempts = {"n": 0}

        def _build_req(effort: str) -> ChatRequest:
            attempts["n"] += 1
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMExtractorItem]:
            parsed = coerce_results_wrapper(_parse_llm_json(text))
            validated = LLMExtractorBatchResponse.model_validate(parsed)
            assert_exact_id_coverage([r.unit_id for r in validated.results], expected_ids, "Extractor")
            return {r.unit_id: r for r in validated.results}

        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse, first_effort="high", label="BFI binding extractor (Agent 1)"
        )
        retry_count = max(attempts["n"] - 1, 0)
        return (ladder_res[0] if ladder_res is not None else {}, retry_count)


async def _evaluate_verifier_batch(
    batch_units: list[dict[str, Any]],
    extractor_results: dict[str, LLMExtractorItem],
    system_prompt: str,
    provider_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, LLMVerifierItem], int]:
    async with semaphore:
        expected_ids = {u["unit_id"] for u in batch_units}
        # Invariant manifest is in the system prompt (cache-friendly); user message carries only the
        # variable per-batch units plus Agent 1's per-unit proposal.
        prompt_payload = {
            "units": [
                {
                    "unit_id": u["unit_id"],
                    "unit_kind": u["unit_kind"],
                    "prose": u["prose"],
                    "label": u.get("label"),
                    "binding": u.get("binding"),
                    "binding_type": u.get("binding_type"),
                    "agent1_proposed": (
                        extractor_results[u["unit_id"]].model_dump()
                        if u["unit_id"] in extractor_results
                        else None
                    ),
                }
                for u in batch_units
            ],
        }

        attempts = {"n": 0}

        def _build_req(effort: str) -> ChatRequest:
            attempts["n"] += 1
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMVerifierItem]:
            parsed = coerce_results_wrapper(_parse_llm_json(text))
            validated = LLMVerifierBatchResponse.model_validate(parsed)
            assert_exact_id_coverage([r.unit_id for r in validated.results], expected_ids, "Verifier")
            return {r.unit_id: r for r in validated.results}

        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse, first_effort="high", label="BFI binding verifier (Agent 2)"
        )
        retry_count = max(attempts["n"] - 1, 0)
        return (ladder_res[0] if ladder_res is not None else {}, retry_count)


async def resolve_residual_unit_bindings(
    db: Any,
    snapshot_id: str,
    residual_units: list[dict[str, Any]],
    manifest_paths: list[str],
    provider_id: str,
) -> dict[str, BindingResolutionResult]:
    """Execute double-agent LLM binding resolution for residual BD units."""
    if not residual_units:
        return {}

    manifest_set = set(manifest_paths)
    manifest_info = [
        {"rel_path": p, "role_hint": _role_hint_for_rel_path(p)}
        for p in sorted(manifest_paths)
    ]
    # The manifest is invariant across every batch of this run, so it goes in the (cacheable) system
    # prompt prefix rather than being re-sent in each batch's user message.
    manifest_block = (
        "\n\nMANIFEST FILES (closed list — candidate/confirmed rel_paths MUST be a subset of these):\n"
        + json.dumps(manifest_info, indent=2)
    )
    extractor_system = _EXTRACTOR_SYSTEM_PROMPT + manifest_block
    verifier_system = _VERIFIER_SYSTEM_PROMPT + manifest_block

    batches = [residual_units[i : i + _BATCH_SIZE] for i in range(0, len(residual_units), _BATCH_SIZE)]
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    # Extractor -> Verifier are pipelined PER BATCH: a batch's verifier fires as soon as ITS OWN
    # extractor returns (the verifier only needs that batch's extractor output, not everyone's), and
    # every batch-chain runs concurrently under the shared semaphore — no global barrier between the
    # two agents. Units are independent across batches, so this is the maximal safe parallelism.
    async def _resolve_batch_chain(b: list[dict[str, Any]]):
        ext_res, ext_retry = await _evaluate_extractor_batch(b, extractor_system, provider_id, semaphore)
        ver_res, ver_retry = await _evaluate_verifier_batch(b, ext_res, verifier_system, provider_id, semaphore)
        return ext_res, ver_res, ext_retry + ver_retry

    chain_outputs = await asyncio.gather(
        *[_resolve_batch_chain(b) for b in batches], return_exceptions=True
    )

    extractor_results: dict[str, LLMExtractorItem] = {}
    verifier_results: dict[str, LLMVerifierItem] = {}
    retries_by_unit: dict[str, int] = {}
    for batch, out in zip(batches, chain_outputs):
        if isinstance(out, Exception):
            logger.warning(f"BFI binding resolver batch chain failed: {out!r}")
            continue
        ext_res, ver_res, retries = out
        extractor_results.update(ext_res)
        verifier_results.update(ver_res)
        for u in batch:
            retries_by_unit[u["unit_id"]] = retries

    # Step 3: Deterministic Fusion & Manifest Intersection
    final_results: dict[str, BindingResolutionResult] = {}

    for u in residual_units:
        uid = u["unit_id"]
        a1 = extractor_results.get(uid)
        a2 = verifier_results.get(uid)
        total_retries = retries_by_unit.get(uid, 0)

        a1_dict = a1.model_dump() if a1 else None
        a2_dict = a2.model_dump() if a2 else None

        if a2 is not None:
            # Agent 2 verifier decision
            confirmed = [p for p in a2.confirmed_rel_paths if p in manifest_set]
            if confirmed and not a2.no_file:
                final_results[uid] = BindingResolutionResult(
                    unit_id=uid,
                    confirmed_rel_paths=confirmed,
                    no_file=False,
                    reason=a2.reason,
                    agent1_raw=a1_dict,
                    agent2_raw=a2_dict,
                    retry_count=total_retries,
                )
            else:
                final_results[uid] = BindingResolutionResult(
                    unit_id=uid,
                    confirmed_rel_paths=[],
                    no_file=True,
                    reason=a2.reason if a2.no_file else "VERIFIER_REJECTED_CANDIDATES",
                    agent1_raw=a1_dict,
                    agent2_raw=a2_dict,
                    retry_count=total_retries,
                )
        else:
            # FIX 3: never confirm off Agent 1 alone — a missing/failed Agent 2 (transport failure,
            # not a validated judgment) must NOT fall back to Agent 1's UNVERIFIED candidates, and
            # must NOT be reported as the factual claim "no file exists" (no_file=True). This holds
            # whether Agent 1 succeeded or also failed.
            final_results[uid] = BindingResolutionResult(
                unit_id=uid,
                confirmed_rel_paths=[],
                no_file=False,
                reason="LLM_NO_RESPONSE",
                agent1_raw=a1_dict,
                agent2_raw=None,
                retry_count=total_retries,
            )

    return final_results
