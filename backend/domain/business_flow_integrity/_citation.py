"""Deterministic citation gate validation for Phase 5 Source-Aware Verifier (Ticket P5-2)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResolvedCitation:
    valid: bool
    rel_path: str
    line_start: int
    line_end: int
    fetched_text: str | None  # what is ACTUALLY at that location (None if invalid)
    source_sha256: str | None
    reject_reason: str | None  # NOT_IN_MANIFEST | RANGE_OUT_OF_FILE | SPAN_TOO_LONG | FILE_UNREADABLE


async def resolve_citation(
    db: Any, snapshot_id: str, rel_path: str, line_start: int, line_end: int
) -> ResolvedCitation:
    """Validate model-provided citation coordinates and mechanically fetch exact verbatim text."""
    clean_path = rel_path.strip()

    # 1. Manifest existence check
    async with db.execute(
        "SELECT 1 FROM manifest_files WHERE snapshot_id=? AND rel_path=?",
        (snapshot_id, clean_path),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=None,
            reject_reason="NOT_IN_MANIFEST",
        )

    # 2. Locate snapshot root on disk
    async with db.execute(
        "SELECT local_path FROM repo_snapshots WHERE id=?",
        (snapshot_id,),
    ) as cur:
        snap_row = await cur.fetchone()

    if not snap_row or not snap_row["local_path"]:
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=None,
            reject_reason="FILE_UNREADABLE",
        )

    file_path = Path(snap_row["local_path"]) / clean_path
    if not file_path.is_file():
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=None,
            reject_reason="FILE_UNREADABLE",
        )

    try:
        raw_bytes = file_path.read_bytes()
    except Exception:
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=None,
            reject_reason="FILE_UNREADABLE",
        )

    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
    total_lines = len(lines)

    # 3. Line bounds check
    if line_start < 1 or line_end < line_start or line_end > total_lines:
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=source_sha256,
            reject_reason="RANGE_OUT_OF_FILE",
        )

    # 4. Span cap check (line_end - line_start + 1 <= 40)
    span_len = line_end - line_start + 1
    if span_len > 40:
        return ResolvedCitation(
            valid=False,
            rel_path=clean_path,
            line_start=line_start,
            line_end=line_end,
            fetched_text=None,
            source_sha256=source_sha256,
            reject_reason="SPAN_TOO_LONG",
        )

    # 5. Extract verbatim text
    fetched_lines = lines[line_start - 1 : line_end]
    fetched_text = "\n".join(fetched_lines)

    return ResolvedCitation(
        valid=True,
        rel_path=clean_path,
        line_start=line_start,
        line_end=line_end,
        fetched_text=fetched_text,
        source_sha256=source_sha256,
        reject_reason=None,
    )
