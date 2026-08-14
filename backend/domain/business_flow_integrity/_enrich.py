"""Single-agent LLM binding enricher and validator for all BD units (Ticket P6 Stage 2).

Replaces the double-agent binding resolver with a single agent that runs on ALL units (not just residual).
Reads BD flow/unit prose + label + binding + binding_type + deterministic seed paths and confirms,
corrects, or enriches the backing manifest source files that realize them.
"""
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


class LLMEnrichItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    confirmed_rel_paths: list[str]
    no_file: bool
    reason: str


class LLMEnrichBatchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMEnrichItem]


@dataclass(frozen=True)
class EnrichResult:
    unit_id: str
    confirmed_rel_paths: list[str]  # Sub-set of manifest rel_paths
    no_file: bool
    reason: str
    agent_raw: dict[str, Any] | None
    retry_count: int


_ENRICHER_SYSTEM_PROMPT = """You are a mainframe codebase binding enricher and validator. Your task is to examine \
business flow units (BD step or branch prose + label + binding string + deterministic seed paths) and confirm, \
correct, or enrich the backing manifest source files that realize them.

Rules:
1. You are given a closed list of manifest source files with role hints (in this system prompt below).
2. confirmed_rel_paths MUST be a strict subset of the given manifest rel_path strings. NEVER invent or hallucinate \
filenames not in the manifest.
3. The deterministic seed is given as the starting point: keep seeds that are right, remove wrong ones, and ADD \
any missing backing files justified by the unit's prose/label/binding.
4. If the unit describes a screen, menu, or panel and a .pfd or .ipf file of that name IS in the manifest, that panel \
file itself is the backing file — confirm it. Do NOT set no_file merely because no program "loads" the panel.
5. Only set no_file to true when the unit describes a genuinely EXTERNAL system, a dataset/library name, or an \
abstract capability with no backing file in the manifest. Give a clear reason.
6. Output ONLY a single JSON object with EXACTLY this top-level shape — no wrapper object, no extra keys:
{
  "results": [
    {
      "unit_id": "bs:example-1",
      "confirmed_rel_paths": ["HNIXLOT.cbl"],
      "no_file": false,
      "reason": "Confirmed HNIXLOT.cbl as the backing COBOL program from seed."
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


async def _evaluate_enrich_batch(
    batch_units: list[dict[str, Any]],
    system_prompt: str,
    provider_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, LLMEnrichItem], int]:
    async with semaphore:
        expected_ids = {u["unit_id"] for u in batch_units}
        prompt_payload = {
            "units": [
                {
                    "unit_id": u["unit_id"],
                    "unit_kind": u["unit_kind"],
                    "prose": u["prose"],
                    "label": u.get("label"),
                    "binding": u.get("binding"),
                    "binding_type": u.get("binding_type"),
                    "seed_rel_paths": u.get("rel_paths", []),
                }
                for u in batch_units
            ]
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

        def _parse(text: str) -> dict[str, LLMEnrichItem]:
            parsed = coerce_results_wrapper(_parse_llm_json(text))
            validated = LLMEnrichBatchResponse.model_validate(parsed)
            assert_exact_id_coverage([r.unit_id for r in validated.results], expected_ids, "BFI enricher")
            return {r.unit_id: r for r in validated.results}

        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse, first_effort="high", label="BFI binding enricher (LLM#1)"
        )
        retry_count = max(attempts["n"] - 1, 0)
        return (ladder_res[0] if ladder_res is not None else {}, retry_count)


async def enrich_unit_bindings(
    db: Any,
    snapshot_id: str,
    units: list[dict[str, Any]],
    manifest_paths: list[str],
    provider_id: str,
) -> dict[str, EnrichResult]:
    """Execute LLM #1 binding enricher + validator for all units."""
    if not units:
        return {}

    manifest_set = set(manifest_paths)
    manifest_info = [
        {"rel_path": p, "role_hint": _role_hint_for_rel_path(p)}
        for p in sorted(manifest_paths)
    ]
    manifest_block = (
        "\n\nMANIFEST FILES (closed list — confirmed_rel_paths MUST be a subset of these):\n"
        + json.dumps(manifest_info, indent=2)
    )
    system_prompt = _ENRICHER_SYSTEM_PROMPT + manifest_block

    batches = [units[i : i + _BATCH_SIZE] for i in range(0, len(units), _BATCH_SIZE)]
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    tasks = [_evaluate_enrich_batch(b, system_prompt, provider_id, semaphore) for b in batches]
    batch_outputs = await asyncio.gather(*tasks, return_exceptions=True)

    enrich_items: dict[str, LLMEnrichItem] = {}
    retries_by_unit: dict[str, int] = {}

    for batch, out in zip(batches, batch_outputs):
        if isinstance(out, Exception):
            logger.warning(f"BFI enricher batch failed: {out!r}")
            continue
        batch_res, retries = out
        enrich_items.update(batch_res)
        for u in batch:
            retries_by_unit[u["unit_id"]] = retries

    final_results: dict[str, EnrichResult] = {}
    for u in units:
        uid = u["unit_id"]
        item = enrich_items.get(uid)
        retries = retries_by_unit.get(uid, 0)

        if item is not None:
            raw_dict = item.model_dump()
            confirmed = [p for p in item.confirmed_rel_paths if p in manifest_set]
            if confirmed and not item.no_file:
                final_results[uid] = EnrichResult(
                    unit_id=uid,
                    confirmed_rel_paths=confirmed,
                    no_file=False,
                    reason=item.reason,
                    agent_raw=raw_dict,
                    retry_count=retries,
                )
            else:
                final_results[uid] = EnrichResult(
                    unit_id=uid,
                    confirmed_rel_paths=[],
                    no_file=True,
                    reason=item.reason if item.no_file else "ENRICHER_NO_VALID_PATHS",
                    agent_raw=raw_dict,
                    retry_count=retries,
                )
        else:
            final_results[uid] = EnrichResult(
                unit_id=uid,
                confirmed_rel_paths=[],
                no_file=False,
                reason="LLM_NO_RESPONSE",
                agent_raw=None,
                retry_count=retries,
            )

    return final_results
