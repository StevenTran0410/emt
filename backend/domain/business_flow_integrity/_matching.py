"""Matcher agent for Phase 6 (Ticket P6 Stage 4).

Reads BD flow/unit prose + provenance + expanded source snippets, determines which snippet(s)
realize the unit, and returns subclaims + citations (locations only).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from shared.logger import logger

from ..doc_graph._llm_citation import _parse_llm_json
from ..model_connector.types import ChatMessage, ChatRequest
from ._llm import LLM_CONCURRENCY, assert_exact_id_coverage, call_with_reasoning_ladder, coerce_results_wrapper
from ._retrieval import Snippet

_BATCH_SIZE = 5


class LLMCitationItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    rel_path: str
    line_start: int
    line_end: int


class LLMMatchItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    unit_id: str
    subclaims: list[str]
    citations: list[LLMCitationItem]
    maps_to_code: bool
    reason: str


class LLMBatchMatchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[LLMMatchItem]


@dataclass(frozen=True)
class MatchResult:
    unit_id: str
    subclaims: list[str]
    citations: list[dict[str, Any]]
    maps_to_code: bool
    reason: str
    agent_raw: dict[str, Any] | None = None
    retry_count: int = 0


_MATCHER_SYSTEM_PROMPT = """You are a mainframe codebase business flow matcher. For each business unit (a BD step \
or branch), you are given its prose description and source code snippets. Decide which snippet(s) realize this \
BD unit.

Rules:
1. Use ONLY the given snippets as evidence.
2. If snippet(s) realize the BD unit, set maps_to_code to true, list short atomic subclaims describing what is \
matched, and cite LOCATIONS ONLY (rel_path + line_start + line_end) where the behavior is found.
3. The system will independently fetch the verbatim text for citations. Citations MUST refer only to locations \
within the provided snippets.
4. If no snippet realizes the unit, set maps_to_code to false, empty citations ([]), and provide a reason.
5. Every unit_id given to you MUST appear EXACTLY ONCE in your results.
6. Respond ONLY in English. Output ONLY a single JSON object with EXACTLY this top-level shape — no wrapper object, \
no extra keys:
{
  "results": [
    {
      "unit_id": "bs:example-1",
      "subclaims": ["The menu option 1 routes directly to PHNIXLOT."],
      "citations": [{"rel_path": "HSBMENU5.pfd", "line_start": 12, "line_end": 18}],
      "maps_to_code": true,
      "reason": "Found menu selection handler mapping option 1 to PHNIXLOT in HSBMENU5.pfd."
    }
  ]
}
"""


async def _evaluate_matcher_batch(
    batch_units: list[dict[str, Any]],
    snippets_by_unit: dict[str, list[Snippet]],
    provider_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, MatchResult], dict[str, Any]]:
    async with semaphore:
        alias_of = {u["unit_id"]: f"u{i+1}" for i, u in enumerate(batch_units)}
        real_of = {f"u{i+1}": u["unit_id"] for i, u in enumerate(batch_units)}
        expected_aliases = set(alias_of.values())

        prompt_payload = [
            {
                "unit_id": alias_of[u["unit_id"]],
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
                    for s in snippets_by_unit.get(u["unit_id"], [])
                ],
            }
            for u in batch_units
        ]

        attempts = {"n": 0}

        def _build_req(effort: str) -> ChatRequest:
            attempts["n"] += 1
            return ChatRequest(
                provider_id=provider_id,
                messages=[
                    ChatMessage(role="system", content=_MATCHER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=json.dumps(prompt_payload, indent=2)),
                ],
                stream=True,
                max_completion_tokens=50000,
                temperature=0.0,
                json_mode=True,
                reasoning_effort=effort,
            )

        def _parse(text: str) -> dict[str, LLMMatchItem]:
            parsed = coerce_results_wrapper(_parse_llm_json(text))
            validated = LLMBatchMatchResponse.model_validate(parsed)
            assert_exact_id_coverage([r.unit_id for r in validated.results], expected_aliases, "BFI matcher")
            return {r.unit_id: r for r in validated.results}

        start = time.monotonic()
        ladder_res = await call_with_reasoning_ladder(
            _build_req, _parse, first_effort="high", label="BFI matcher (LLM#2)"
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        retry_count = max(attempts["n"] - 1, 0)
        meta = {"retry_count": retry_count, "latency_ms": latency_ms}

        if ladder_res is None:
            return {
                u["unit_id"]: MatchResult(
                    unit_id=u["unit_id"],
                    subclaims=[],
                    citations=[],
                    maps_to_code=False,
                    reason="LLM_NO_RESPONSE",
                    agent_raw=None,
                    retry_count=retry_count,
                )
                for u in batch_units
            }, meta

        parsed_items, _ = ladder_res
        results: dict[str, MatchResult] = {}
        for alias_id, item in parsed_items.items():
            real_uid = real_of.get(alias_id, alias_id)
            results[real_uid] = MatchResult(
                unit_id=real_uid,
                subclaims=item.subclaims,
                citations=[c.model_dump() for c in item.citations],
                maps_to_code=item.maps_to_code,
                reason=item.reason,
                agent_raw=item.model_dump(),
                retry_count=retry_count,
            )
        return results, meta


async def run_unit_matching(
    batch_units: list[dict[str, Any]],
    snippets_by_unit: dict[str, list[Snippet]],
    provider_id: str,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[dict[str, MatchResult], dict[str, Any]]:
    """Execute LLM #2 matching for a batch of ready units."""
    if not batch_units:
        return {}, {}
    if semaphore is None:
        semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    return await _evaluate_matcher_batch(batch_units, snippets_by_unit, provider_id, semaphore)
