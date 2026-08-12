"""BD Flow LLM overlay — Stage 1: chunk whitelisted free-prose regions of a BD ParsedDoc.

Whitelist is dataset-fitted to EMT.BD-*.report.md's section vocabulary (same pragmatism as the
B1 deterministic extractor in _flow_extract.py): per-step Step Details, Execution Sequence table
footnotes, per-event Event Details ('System Behavior' numbered lists), and Job Flow Failure Modes.
Everything else in the BD is out of scope for the LLM pass — B1 already covers it deterministically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import ParsedDoc

MAX_CHUNK_LINES = 40

# Canonical (numbering-stripped, lowercased) heading titles this pass is allowed to read prose from.
_STEP_DETAILS_TITLE = "step details"
_FAILURE_MODES_TITLE = "failure modes & error handling"
_EVENT_DETAILS_TITLE = "event details"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_NUMBERING_RE = re.compile(r"^\d+(\.\d+)*\.?\s*")


@dataclass(frozen=True)
class ProseChunk:
    text: str
    line_start: int  # 1-based, inclusive
    line_end: int  # 1-based, inclusive
    region_kind: str  # step_details | exec_sequence_footnote | event_system_behavior | failure_modes


def _clean_title(title: str) -> str:
    """Strip leading '5.2.'-style numbering and markdown bold so headings compare by wording only."""
    t = _NUMBERING_RE.sub("", title.strip())
    return t.replace("*", "").strip().lower()


def _headings(lines: list[str]) -> list[tuple[int, str]]:
    """All (1-based line, cleaned title) heading positions, any level."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        m = _HEADING_RE.match(line)
        if m:
            out.append((i, _clean_title(m.group(2))))
    return out


def _next_heading_line(headings: list[tuple[int, str]], after_line: int, total_lines: int) -> int:
    for hl, _title in headings:
        if hl > after_line:
            return hl
    return total_lines + 1


def _trim_blank_edges(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """Drop leading/trailing blank lines so a region's coordinates bound only real content."""
    while start <= end and lines[start - 1].strip() == "":
        start += 1
    while end >= start and lines[end - 1].strip() == "":
        end -= 1
    return start, end


def _paragraphs(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Contiguous non-blank line runs within [start, end] — the chunk-split units."""
    paras: list[tuple[int, int]] = []
    cur_start: int | None = None
    for i in range(start, end + 1):
        if lines[i - 1].strip() == "":
            if cur_start is not None:
                paras.append((cur_start, i - 1))
                cur_start = None
        elif cur_start is None:
            cur_start = i
    if cur_start is not None:
        paras.append((cur_start, end))
    return paras


def _make_chunk(lines: list[str], start: int, end: int, region_kind: str) -> ProseChunk:
    text = "\n".join(lines[start - 1 : end])
    return ProseChunk(text=text, line_start=start, line_end=end, region_kind=region_kind)


def _split_region(lines: list[str], start: int, end: int, region_kind: str) -> list[ProseChunk]:
    """Greedily pack paragraphs into <=MAX_CHUNK_LINES chunks; never split inside a paragraph."""
    paras = _paragraphs(lines, start, end)
    if not paras:
        return []
    chunks: list[ProseChunk] = []
    cs, ce = paras[0]
    for ps, pe in paras[1:]:
        if pe - cs + 1 <= MAX_CHUNK_LINES:
            ce = pe
        else:
            chunks.append(_make_chunk(lines, cs, ce, region_kind))
            cs, ce = ps, pe
    chunks.append(_make_chunk(lines, cs, ce, region_kind))
    return chunks


def _table_footnote_regions(
    doc: ParsedDoc, lines: list[str], headings: list[tuple[int, str]]
) -> list[tuple[int, int]]:
    """Execution Sequence table footnote prose: text directly after such a table, before the next heading."""
    total_lines = len(lines)
    regions: list[tuple[int, int]] = []
    for table in doc.tables:
        headers = [h.strip() for h in table.get("headers", [])]
        if not (
            "Step Name" in headers
            and "Pass / Iteration" in headers
            and "Program / Procedure" in headers
        ):
            continue
        t_line = table.get("line_start", 1)
        last_table_line = t_line
        k = t_line + 1
        while k <= total_lines and lines[k - 1].strip().startswith("|"):
            last_table_line = k
            k += 1
        footnote_start = last_table_line + 1
        footnote_end = _next_heading_line(headings, last_table_line, total_lines) - 1
        if footnote_start <= footnote_end:
            regions.append((footnote_start, footnote_end))
    return regions


def select_prose_chunks(doc: ParsedDoc) -> list[ProseChunk]:
    """Chunk the BD's whitelisted free-prose regions for the LLM overlay pass.

    Locates regions by heading titles + table boundaries in doc.raw_content, mirroring how B1
    locates its skeleton facts. Chunks are <=40 lines and only ever split on paragraph (blank-line)
    boundaries, so a chunk never cuts a sentence block in half.
    """
    lines = doc.raw_content.splitlines()
    if not lines:
        return []
    total_lines = len(lines)
    headings = _headings(lines)

    chunks: list[ProseChunk] = []

    for hl, title in headings:
        if title == _STEP_DETAILS_TITLE:
            region_kind = "step_details"
        elif title == _FAILURE_MODES_TITLE:
            region_kind = "failure_modes"
        elif title == _EVENT_DETAILS_TITLE:
            region_kind = "event_system_behavior"
        else:
            continue
        end = _next_heading_line(headings, hl, total_lines) - 1
        start, end = _trim_blank_edges(lines, hl, end)
        if start <= end:
            chunks.extend(_split_region(lines, start, end, region_kind))

    for start, end in _table_footnote_regions(doc, lines, headings):
        start, end = _trim_blank_edges(lines, start, end)
        if start <= end:
            chunks.extend(_split_region(lines, start, end, "exec_sequence_footnote"))

    chunks.sort(key=lambda c: c.line_start)
    return chunks
