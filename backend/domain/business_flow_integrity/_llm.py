"""Shared reasoning-tier retry ladder for BFI/doc-graph LLM classification calls (TICKET P5-0).

Judgment-tier call sites open at reasoning_effort="high"; prose-tier sites open at "low" (see
_map.py, _verdict.py, _narrative.py, _summary.py, doc_graph/_flow_group.py). Either way, a response
that comes back empty or fails schema/pydantic validation gets exactly one retry at "low" (a
smaller thinking budget leaves more headroom for the answer) before the caller's own deterministic
fallback takes over. A stream/transport error is NOT retried — it aborts straight to the fallback,
matching each call site's pre-existing behavior for that failure mode.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import ValidationError

from shared.logger import logger

from ..model_connector.service import ProviderConfigService
from ..model_connector.types import ChatRequest

T = TypeVar("T")

RETRY_EFFORT = "low"

# Max provider calls in flight per BFI phase. This is a self-imposed throttle, NOT a provider limit;
# with the streamed-429 handler fixed, it can run well above the old hardcoded 5. Tune here in one place.
LLM_CONCURRENCY = 15


def assert_exact_id_coverage(got_ids: Sequence[str], expected_ids: set[str], label: str) -> None:
    """Reject dup/extra/missing unit_ids (P5-REVIEW-FIXES FIX 7): a plain set comparison lets a
    reply like [A, B, B] pass ({A,B}=={A,B}) and the duplicate silently overwrites the first via a
    dict comprehension. Require exact 1:1 coverage instead; any mismatch raises so the ladder retries."""
    counts = Counter(got_ids)
    if len(got_ids) != len(expected_ids) or set(counts) != expected_ids or any(c != 1 for c in counts.values()):
        raise ValueError(
            f"{label} unit_id coverage mismatch: expected {sorted(expected_ids)}, got {sorted(got_ids)}"
        )


def coerce_results_wrapper(parsed: Any) -> Any:
    """Best-effort normalize a batched LLM reply to the {"results": [...]} envelope the schemas
    expect, tolerating the two shapes deepseek most often emits instead: a bare list of items, or a
    single item object at the top level. Anything else is returned untouched so validation can fail
    and the ladder can retry."""
    if isinstance(parsed, list):
        return {"results": parsed}
    if isinstance(parsed, dict) and "results" not in parsed and "unit_id" in parsed:
        return {"results": [parsed]}
    return parsed


async def _resolve_pinned_effort(provider_id: str | None) -> str | None:
    """If the provider config pins reasoning via extra.default_reasoning_effort, that value wins over
    the call site's first_effort (the adapter already forces it onto the wire, so the ladder must
    match it — otherwise the log and retry pretend an effort the provider never actually uses)."""
    if not provider_id:
        return None
    try:
        cfg = await ProviderConfigService().get_by_id(provider_id)  # masked: no api_key, keeps effort
        pinned = (cfg.extra or {}).get("default_reasoning_effort")
        return str(pinned).lower() if pinned else None
    except Exception:
        return None


async def call_with_reasoning_ladder(
    build_request: Callable[[str], ChatRequest],
    parse: Callable[[str], T],
    *,
    first_effort: str,
    label: str,
    provider_id: str | None = None,
) -> tuple[T, str] | None:
    """Stream a chat call; on empty content or a `parse` failure (ValueError or pydantic
    ValidationError), retry exactly once. Returns (parsed_result, raw_text) on success, else None so
    the caller applies its own deterministic fallback. Never caches anything itself — a failed attempt
    is simply discarded, never persisted.

    Effort ladder: if the provider pins extra.default_reasoning_effort, both attempts run at that
    pinned tier (the adapter forces it anyway). Otherwise the call site's `first_effort` opens and the
    retry drops to 'low' for extra answer headroom."""
    pinned = await _resolve_pinned_effort(provider_id)
    efforts = (pinned, pinned) if pinned else (first_effort, RETRY_EFFORT)
    for attempt, effort in enumerate(efforts):
        req = build_request(effort)
        full_text = ""
        try:
            async for evt in ProviderConfigService().chat_stream_events(req):
                if evt.get("type") == "content":
                    full_text += evt.get("text") or ""
        except Exception as e:
            # Transient transport/stream errors (dropped stream, surfaced 429, etc.) get the same one
            # retry as empty/invalid replies instead of dropping the whole batch to fallback.
            logger.warning(f"{label} LLM stream error (effort={effort}): {e}")
            if attempt == 0:
                continue
            return None

        if not full_text.strip():
            logger.warning(f"{label} LLM call returned empty content (effort={effort})")
            if attempt == 0:
                continue
            return None

        try:
            return parse(full_text), full_text
        except (ValueError, ValidationError) as ve:
            logger.warning(f"{label} LLM parse/validation error (effort={effort}): {ve}")
            if attempt == 0:
                continue
            return None
    return None
