"""Text normalization, chunk-size heuristics, and boundary-expansion helpers."""

from __future__ import annotations

import hashlib
import re

from ..types import RetrievalEvidence
from ._constants import _BRACE_LANGS, _WS


def _normalize_text(s: str) -> str:
    return _WS.sub(" ", s).strip()


def _token_estimate(s: str) -> int:
    # Fast proxy; good enough for budgeting.
    return max(1, len(s) // 4)


def _chunk_size_for(category: str, language: str | None) -> int:
    if category == "docs":
        return 1800
    if category == "config":
        return 1200
    if category == "test":
        return 1400
    if language in {"python", "typescript", "javascript", "ruby", "elixir", "julia"}:
        return 1500
    if language in {"haskell", "ocaml", "erlang"}:
        return 1400
    return 1300


def _ends_mid_function(content: str, language: str | None) -> bool:
    """Return True if the chunk likely ends in the middle of a function/class body."""
    if not content.strip():
        return False

    if (language or "").lower() in _BRACE_LANGS:
        return content.count("{") > content.count("}")

    if (language or "").lower() == "python":
        lines = content.splitlines()
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return False
        has_def = any(re.match(r"[ \t]*(def |class |async def )", line) for line in lines)
        last = non_empty[-1]
        # If the last line is still indented and the chunk has a def/class, likely mid-body
        return has_def and (last.startswith(" ") or last.startswith("\t"))

    # Default: brace balance
    return content.count("{") > content.count("}")


def _split_chunks(text: str, target_size: int) -> list[str]:
    clean = text.replace("\r\n", "\n")
    if len(clean) <= target_size:
        return [clean]
    out: list[str] = []
    start = 0
    overlap = max(120, target_size // 8)
    while start < len(clean):
        end = min(len(clean), start + target_size)
        out.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return out


def _maybe_expand_to_boundary(
    ev: RetrievalEvidence,
    chunk_lookup: dict[tuple[str, int], dict],
) -> RetrievalEvidence:
    """If the chunk ends mid-function, append the next adjacent chunk once (at most one hop, never chained)."""
    cur_row = chunk_lookup.get((ev.rel_path, ev.chunk_index))
    language = cur_row["language"] if cur_row is not None else None
    if not _ends_mid_function(ev.excerpt, language):
        return ev

    next_row = chunk_lookup.get((ev.rel_path, ev.chunk_index + 1))
    if next_row is None or not next_row["content"]:
        return ev  # No adjacent chunk available

    merged = ev.excerpt + "\n" + (next_row["content"] or "")
    return RetrievalEvidence(
        chunk_id=ev.chunk_id,
        rel_path=ev.rel_path,
        chunk_index=ev.chunk_index,
        reason_codes=[*ev.reason_codes, "boundary-expanded"],
        score=ev.score,
        token_estimate=_token_estimate(merged),
        excerpt=merged,
    )


def _compute_content_hash(text: str) -> str:
    """Compute MD5 hash of first 4KB of content for cheap change detection."""
    prefix = text[:4096]
    return hashlib.md5(prefix.encode("utf-8", errors="replace")).hexdigest()
