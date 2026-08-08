"""Markdown parser for BD and DD documentation reports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .types import ParsedDoc, SectionMapInfo

_HEADING_REGEX = re.compile(r"^(#{1,4})\s+(?:(\d+(?:\.\d+)*)\.?\s+)?(.+?)\s*$")
_TABLE_ROW_REGEX = re.compile(r"^\s*\|(.+)\|\s*$")

_BR_REGEX = re.compile(r"\bBR-\d{1,}\b", re.IGNORECASE)
_TBD_REGEX = re.compile(r"\bTBD-\d{1,}\b", re.IGNORECASE)
_LIMIT_REGEX = re.compile(r"\bL-\d{1,}\b", re.IGNORECASE)
_DSN_REGEX = re.compile(r"\b[A-Z][A-Z0-9]*(?:\.[A-Z0-9#$@-]{1,8})+\b")
_PROGRAM_ID_REGEX = re.compile(r"\b[A-Z][A-Z0-9]{2,7}\b")
_LINE_REF_REGEX = re.compile(r"\bL\d+(?:-\d+)?\b")

# Citation regexes
_CITE_SECTION_REGEX = re.compile(
    r"(GEN\.DD\.(?:COBOL|JCL)-[A-Z0-9_-]+\.report\.md)(?:\s+§(\d+(?:\.\d+)*))?"
)
_CITE_STEP_REGEX = re.compile(
    r"(GEN\.DD\.(?:COBOL|JCL)-[A-Z0-9_-]+\.report\.md)\s+step\s+([\d.]+(?:/[\d.]+)*)", re.IGNORECASE
)
_CITE_UNNUMBERED_REGEX = re.compile(
    r"(GEN\.DD\.(?:COBOL|JCL)-[A-Z0-9_-]+\.report\.md)\s+§(\d+)\s+([A-Za-z0-9_-]+)", re.IGNORECASE
)


def classify_doc_kind(filename: str) -> str:
    """Classify markdown report kind from filename."""
    fname = filename.upper()
    is_bd = "BD" in fname and "DD" not in fname
    is_dd_cobol = ("DD.COBOL-" in fname or "DD-COBOL" in fname) and "BD" not in fname
    is_dd_jcl = ("DD.JCL-" in fname or "DD-JCL" in fname) and "BD" not in fname

    if is_dd_cobol and not is_bd and not is_dd_jcl:
        return "dd_cobol"
    elif is_dd_jcl and not is_bd and not is_dd_cobol:
        return "dd_jcl"
    elif is_bd and not is_dd_cobol and not is_dd_jcl:
        return "bd"
    raise ValueError(f"Ambiguous or unrecognized document kind for file: {filename}")


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", cleaned)


def parse_markdown_report(file_path: str, content: str, content_sha256: str) -> ParsedDoc:
    """Parse BD/DD markdown document into structured sections, tables, labeled IDs, and
    mermaid diagrams."""
    path_obj = Path(file_path)
    artifact_name = path_obj.name
    doc_kind = classify_doc_kind(artifact_name)
    doc_id = f"doc/{artifact_name}"

    lines = content.splitlines()

    headings: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    labeled_ids: list[dict[str, Any]] = []
    mermaid_diagrams: list[str] = []

    current_section_id = "root"
    current_section_title = "Root"
    section_ordinal = 0

    # Stack to track section hierarchy for unnumbered slug IDs
    parent_stack: list[tuple[int, str]] = []

    in_mermaid = False
    current_mermaid_lines: list[str] = []

    # N3: Extract labeled IDs across all lines with scoping and deduplication
    seen_labeled_keys: set[str] = set()
    for idx, l_text in enumerate(lines, 1):
        for br in _BR_REGEX.findall(l_text):
            br_u = br.upper()
            key = f"br:{br_u}"
            if key not in seen_labeled_keys:
                seen_labeled_keys.add(key)
                labeled_ids.append(
                    {
                        "id": br_u,
                        "kind": "br",
                        "section_id": "root",
                        "line": idx,
                        "text": l_text.strip(),
                    }
                )

        for tbd in _TBD_REGEX.findall(l_text):
            tbd_u = tbd.upper()
            key = f"tbd:{tbd_u}"
            if key not in seen_labeled_keys:
                seen_labeled_keys.add(key)
                labeled_ids.append(
                    {
                        "id": tbd_u,
                        "kind": "tbd",
                        "section_id": "root",
                        "line": idx,
                        "text": l_text.strip(),
                    }
                )

        for lim in _LIMIT_REGEX.findall(l_text):
            lim_u = lim.upper()
            key = f"limit:{lim_u}"
            if key not in seen_labeled_keys:
                seen_labeled_keys.add(key)
                labeled_ids.append(
                    {
                        "id": lim_u,
                        "kind": "limit",
                        "section_id": "root",
                        "line": idx,
                        "text": l_text.strip(),
                    }
                )

    i = 0
    num_lines = len(lines)
    while i < num_lines:
        line = lines[i]

        # Check mermaid fences
        if line.strip().startswith("```mermaid"):
            in_mermaid = True
            current_mermaid_lines = []
            if headings:
                headings[-1]["payload_kind"] = "mermaid"
            i += 1
            continue

        if in_mermaid:
            if line.strip().startswith("```"):
                in_mermaid = False
                mermaid_diagrams.append("\n".join(current_mermaid_lines))
            else:
                current_mermaid_lines.append(line)
            i += 1
            continue

        # Check headings
        h_match = _HEADING_REGEX.match(line)
        if h_match:
            level = len(h_match.group(1))
            num_id = h_match.group(2)
            title = h_match.group(3).strip()

            # D: pop shallower/equal-level ancestors unconditionally — numbered headings
            # must also unwind the stack, else ids accumulate stale ancestor prefixes.
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()

            if num_id:
                sec_id = num_id
            else:
                section_ordinal += 1
                # D: the level-1 document title (H1) is doc root, not a numbered section —
                # exclude it from the prefix chain so e.g. `### Purpose` under `## 3.`
                # gets `3/purpose`, not `<doc-title-slug>/3/purpose`.
                prefix_ancestors = [p[1] for p in parent_stack if p[0] > 1]
                parent_prefix = "/".join(prefix_ancestors)
                slug = _slugify(title)
                sec_id = (
                    f"{parent_prefix}/{slug}"
                    if parent_prefix
                    else (slug or f"sec-{section_ordinal}")
                )

            parent_stack.append((level, sec_id))
            current_section_id = sec_id
            current_section_title = title

            headings.append(
                {
                    "section_id": sec_id,
                    "title": title,
                    "level": level,
                    "line_start": i + 1,
                    "payload_kind": "container",
                }
            )
            i += 1
            continue

        # Check tables
        if _TABLE_ROW_REGEX.match(line):
            if headings and headings[-1]["payload_kind"] == "container":
                headings[-1]["payload_kind"] = "table"

            table_start = i + 1
            table_lines: list[str] = []
            while i < num_lines and _TABLE_ROW_REGEX.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            table_end = i

            parsed_table = _parse_markdown_table(
                table_lines,
                section_id=current_section_id,
                section_title=current_section_title,
                line_start=table_start,
                line_end=table_end,
            )
            if parsed_table:
                tables.append(parsed_table)

                # M7: Restrict steps map to Processing Logic tables (§4.1) and strip @Lnn
                sec_title_u = current_section_title.upper()
                if (
                    "PROCESSING LOGIC" in sec_title_u
                    or "LOGIC STAGES" in sec_title_u
                    or "4.1" in current_section_id
                ):
                    for row in parsed_table.get("rows", []):
                        step_val = (
                            row.get("Step") or row.get("step") or row.get("#") or row.get("Stage")
                        )
                        if step_val:
                            raw_s = str(step_val).strip()
                            clean_s = re.split(r"[\s\(@]", raw_s)[0].strip()
                            steps.append(
                                {
                                    "step_id": clean_s,
                                    "raw_step_id": raw_s,
                                    "section_id": current_section_id,
                                    "line": table_start,
                                    "data": row,
                                }
                            )

            continue

        # Check list or prose content for payload_kind
        if line.strip().startswith(("- ", "* ", "1. ")):
            if headings and headings[-1]["payload_kind"] == "container":
                headings[-1]["payload_kind"] = "labeled_list"
        elif line.strip() and not line.strip().startswith("#"):
            if headings and headings[-1]["payload_kind"] == "container":
                headings[-1]["payload_kind"] = "prose"

        i += 1

    section_map = SectionMapInfo(
        headings=headings,
        steps=steps,
    )

    # §7.7: never hard-fail on a missing/renamed/malformed section — but never silently
    # produce a sparse parse either. A doc with only its H1 (or none) has no discernible
    # numbered/unnumbered section structure at all; record that as a warning.
    warnings: list[str] = []
    if len(headings) <= 1:
        warnings.append(
            f"Only {len(headings)} heading(s) found — expected numbered/unnumbered "
            "sections were not detected; document may be malformed, renamed, or empty."
        )

    return ParsedDoc(
        id=doc_id,
        doc_kind=doc_kind,
        artifact_name=artifact_name,
        doc_path=file_path,
        content_sha256=content_sha256,
        generated_at="",
        section_map=section_map,
        tables=tables,
        labeled_ids=labeled_ids,
        mermaid_diagrams=mermaid_diagrams,
        raw_content=content,
        warnings=warnings,
    )


def _parse_markdown_table(
    table_lines: list[str],
    section_id: str,
    section_title: str,
    line_start: int,
    line_end: int,
) -> dict[str, Any] | None:
    """Parse raw markdown table lines into headers and rows."""
    if len(table_lines) < 2:
        return None

    def split_row(row_str: str) -> list[str]:
        parts = row_str.strip().strip("|").split("|")
        return [p.strip() for p in parts]

    headers = split_row(table_lines[0])

    # Skip separator line (e.g. |---|---|)
    start_idx = 1
    if start_idx < len(table_lines) and re.match(r"^\s*\|?\s*:?-+:?\s*\|", table_lines[start_idx]):
        start_idx += 1

    rows: list[dict[str, str]] = []
    for r_idx in range(start_idx, len(table_lines)):
        cols = split_row(table_lines[r_idx])
        row_dict: dict[str, str] = {}
        for idx, h in enumerate(headers):
            val = cols[idx] if idx < len(cols) else ""
            row_dict[h] = val
        if any(row_dict.values()):
            rows.append(row_dict)

    return {
        "section_id": section_id,
        "section_title": section_title,
        "line_start": line_start,
        "line_end": line_end,
        "headers": headers,
        "rows": rows,
    }


def build_dataset_alias_map(docs: list[ParsedDoc]) -> dict[str, str]:
    """Build dataset alias mapping (friendly names & DDNAMEs -> canonical DSN) from JCL
    DD-Statements tables."""
    alias_map: dict[str, str] = {}

    for doc in docs:
        if doc.doc_kind != "dd_jcl":
            continue

        for table in doc.tables:
            headers_upper = [h.upper() for h in table.get("headers", [])]
            if (
                any("DDNAME" in h for h in headers_upper)
                or any("DSN" in h for h in headers_upper)
                or any("DATASET" in h for h in headers_upper)
                or any("RESOURCE" in h for h in headers_upper)
            ):
                for row in table.get("rows", []):
                    ddname = row.get("DDName") or row.get("DDNAME") or row.get("ddname") or ""
                    dsn_cell = (
                        row.get("DSN")
                        or row.get("Dataset / Resource")
                        or row.get("Dataset")
                        or row.get("Resource")
                        or ""
                    )
                    purpose = row.get("Purpose") or row.get("Description") or ""

                    dsn_match = _DSN_REGEX.search(dsn_cell)
                    if not dsn_match:
                        dsn_match = _DSN_REGEX.search(purpose)

                    if dsn_match:
                        canonical_dsn = dsn_match.group(0).upper()
                        if ddname:
                            alias_map[ddname.upper()] = canonical_dsn

                        # Map friendly names and component suffixes
                        # e.g. TRXFL.SEQ -> AWS.M2.CARDDEMO.TRXFL.SEQ
                        parts = canonical_dsn.split(".")
                        if len(parts) >= 2:
                            alias_map[".".join(parts[-2:])] = canonical_dsn
                        if len(parts) >= 3:
                            alias_map[".".join(parts[-3:])] = canonical_dsn

                        # Only map full DSN-shaped tokens (_DSN_REGEX, unbounded components).
                        # A 2-component regex here fragmented `AWS.M2.CARDDEMO...` into `AWS.M2`
                        # and mapped that short prefix to whichever DSN was processed last —
                        # cross-contaminating unrelated datasets. Suffix/friendly forms are
                        # already mapped above (lines building last-2/last-3 component keys).
                        for token in _DSN_REGEX.findall(dsn_cell + " " + purpose):
                            token_u = token.upper()
                            if token_u != canonical_dsn and not token_u.endswith(".REPORT.MD"):
                                alias_map[token_u] = canonical_dsn

    return alias_map


def parse_mermaid_dataset_accesses(mermaid_content: str) -> list[dict[str, Any]]:
    """Parse dataset edges from mermaid diagrams e.g. B --> D1[(TRNXFILE KSDS)]."""
    accesses: list[dict[str, Any]] = []
    node_map: dict[str, str] = {}

    for m in re.finditer(r"\b([A-Za-z0-9_]+)\[\[?\(?([A-Za-z0-9_.-]+)\)?\]?\]?", mermaid_content):
        node_id = m.group(1)
        node_label = m.group(2)
        node_map[node_id] = node_label

    for line in mermaid_content.splitlines():
        edge_m = re.search(r"\b([A-Za-z0-9_]+)\s*--(?:>|-)\s*([A-Za-z0-9_]+)\b", line)
        if edge_m:
            src = edge_m.group(1)
            dst = edge_m.group(2)
            src_label = node_map.get(src, src)
            dst_label = node_map.get(dst, dst)
            accesses.append({"src": src_label, "dst": dst_label, "line": line.strip()})

    return accesses
