"""Deterministic evidence snippet retrieval for Phase 5 Source-Aware Verifier (Ticket P5-2)."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import os

from pathlib import Path
from typing import Any

from ._evidence import EvidenceOccurrence, load_evidence_for_target


@dataclass(frozen=True)
class Snippet:
    rel_path: str
    line_start: int          # snippet bounds actually cut (occurrence span padded)
    line_end: int
    text: str                # verbatim source lines
    source_sha256: str       # hash of the FULL file bytes (the pin)
    occurrence_id: str       # bfi_evidence_occurrences.id
    parse_status: str        # 'ok' | 'partial'


@dataclass(frozen=True)
class RetrievalResult:
    unit_id: str
    snippets: list[Snippet]          # 1-3, may be empty when abstained
    abstain_reason: str | None       # reason code when snippets is empty


def _extract_target_candidates(unit: dict[str, Any] | Any, target_override: list[str] | None = None) -> list[str]:
    """Extract candidate asset targets/bindings from a unit or explicit override list."""
    candidates: list[str] = []

    if target_override:
        for t in target_override:
            if t and isinstance(t, str):
                cleaned = t.strip()
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)

    if isinstance(unit, dict):
        # Extract from dict
        for key in ("target_bindings", "bindings", "resolved_rel_path", "rel_path", "target"):
            val = unit.get(key)
            if isinstance(val, list):
                for item in val:
                    if item and isinstance(item, str):
                        cleaned = item.strip()
                        if cleaned and cleaned not in candidates:
                            candidates.append(cleaned)
            elif isinstance(val, str) and val.strip():
                cleaned = val.strip()
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)
    elif hasattr(unit, "__dict__"):
        for attr in ("target_bindings", "bindings", "resolved_rel_path", "rel_path", "target"):
            val = getattr(unit, attr, None)
            if isinstance(val, list):
                for item in val:
                    if item and isinstance(item, str):
                        cleaned = item.strip()
                        if cleaned and cleaned not in candidates:
                            candidates.append(cleaned)
            elif isinstance(val, str) and val.strip():
                cleaned = val.strip()
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)

    return candidates


async def retrieve_unit_snippets(
    db: Any,
    snapshot_id: str,
    unit: dict[str, Any] | Any,
    target_override: list[str] | None = None,
) -> RetrievalResult:
    """Retrieve 1-3 pinned evidence snippets for a unit or abstain with reason code."""
    # Extract unit metadata
    unit_id = ""
    unit_kind = "step"

    if isinstance(unit, dict):
        unit_id = str(unit.get("unit_id") or unit.get("id") or "")
        unit_kind = str(unit.get("unit_kind") or "step").lower()
    elif hasattr(unit, "__dict__"):
        unit_id = str(getattr(unit, "unit_id", getattr(unit, "id", "")))
        unit_kind = str(getattr(unit, "unit_kind", "step")).lower()
    elif isinstance(unit, str):
        unit_id = unit

    targets = _extract_target_candidates(unit, target_override=target_override)

    if not targets:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="UNRESOLVED_ASSET")

    # Check for explicit external targets
    has_external = any(t.startswith("__external__/") or t.startswith("__unresolved__/") for t in targets)
    valid_targets = [t for t in targets if not (t.startswith("__external__/") or t.startswith("__unresolved__/"))]

    if not valid_targets and has_external:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="EXTERNAL_TARGET")

    # Query snapshot root directory
    async with db.execute("SELECT local_path FROM repo_snapshots WHERE id=?", (snapshot_id,)) as cur:
        snap_row = await cur.fetchone()

    if not snap_row or not snap_row["local_path"]:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="FILE_UNREADABLE")

    snap_root = Path(snap_row["local_path"])

    # Load candidate occurrences
    candidate_occurrences: list[EvidenceOccurrence] = []
    seen_occ_ids: set[str] = set()

    for target in valid_targets:
        occs = await load_evidence_for_target(db, snapshot_id, target)
        for occ in occs:
            if occ.id not in seen_occ_ids:
                seen_occ_ids.add(occ.id)
                candidate_occurrences.append(occ)

    if not candidate_occurrences:
        if has_external:
            return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="EXTERNAL_TARGET")
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="NO_OCCURRENCE")

    # Sort candidates deterministically:
    # 1. Preferred kind (for branch: guard first; for step: edge/step first)
    # 2. Smallest span (line_end - line_start)
    # 3. line_start
    # 4. occurrence id
    def _rank_candidate(occ: EvidenceOccurrence) -> tuple[int, int, int, str]:
        if unit_kind == "branch":
            kind_priority = 0 if occ.kind == "guard" else 1
        else:
            kind_priority = 0 if occ.kind in ("edge", "step") else 1
        span_len = occ.line_end - occ.line_start
        return (kind_priority, span_len, occ.line_start, occ.id)

    candidate_occurrences.sort(key=_rank_candidate)

    # Cut snippets for top candidates (max 3)
    snippets: list[Snippet] = []

    for occ in candidate_occurrences:
        if len(snippets) >= 3:
            break

        file_path = snap_root / occ.rel_path
        if not file_path.is_file():
            continue

        try:
            raw_bytes = file_path.read_bytes()
        except Exception:
            continue

        source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        text_content = raw_bytes.decode("utf-8", errors="replace")
        lines = text_content.splitlines()
        total_lines = len(lines)

        if total_lines == 0:
            continue

        # Compute padded snippet bounds (occurrence span padded by ±10 lines)
        padded_start = max(1, occ.line_start - 10)
        padded_end = min(total_lines, occ.line_end + 10)

        # Hard cap 1: line count <= 80
        if padded_end - padded_start + 1 > 80:
            padded_end = min(total_lines, padded_start + 79)

        snippet_lines = lines[padded_start - 1 : padded_end]
        snippet_text = "\n".join(snippet_lines)

        # Hard cap 2: char length <= 3000 chars (truncate at line boundary)
        if len(snippet_text) > 3000:
            kept_lines: list[str] = []
            curr_len = 0
            truncated_end = padded_start

            for idx, line in enumerate(snippet_lines):
                line_len = len(line) + (1 if kept_lines else 0)
                if curr_len + line_len > 3000:
                    break
                kept_lines.append(line)
                curr_len += line_len
                truncated_end = padded_start + idx

            if kept_lines:
                kept_lines.append("... [truncated]")
                snippet_text = "\n".join(kept_lines)
                padded_end = truncated_end
            else:
                # First line itself exceeded 3000 chars
                snippet_text = snippet_lines[0][:3000] + "\n... [truncated]"
                padded_end = padded_start

        snippets.append(
            Snippet(
                rel_path=occ.rel_path,
                line_start=padded_start,
                line_end=padded_end,
                text=snippet_text,
                source_sha256=source_sha256,
                occurrence_id=occ.id,
                parse_status=occ.parse_status,
            )
        )

    if not snippets:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="FILE_UNREADABLE")

    return RetrievalResult(unit_id=unit_id, snippets=snippets, abstain_reason=None)


_ALLOWED_BFS_EDGE_TYPES = {"menu_option", "calls", "submits", "executes", "shows_panel", "stacks"}


async def expand_unit_snippets(
    db: Any,
    snapshot_id: str,
    unit: dict[str, Any] | Any,
    rel_paths: list[str] | None = None,
) -> RetrievalResult:
    """Retrieve expanded evidence snippets via 1-hop BFS on code_flow_edges for Phase 6 matcher."""
    # Extract unit metadata
    unit_id = ""
    unit_kind = "step"

    if isinstance(unit, dict):
        unit_id = str(unit.get("unit_id") or unit.get("id") or "")
        unit_kind = str(unit.get("unit_kind") or "step").lower()
    elif hasattr(unit, "__dict__"):
        unit_id = str(getattr(unit, "unit_id", getattr(unit, "id", "")))
        unit_kind = str(getattr(unit, "unit_kind", "step")).lower()
    elif isinstance(unit, str):
        unit_id = unit

    targets = _extract_target_candidates(unit, target_override=rel_paths)

    if not targets:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="UNRESOLVED_ASSET")

    # Check for explicit external targets
    has_external = any(t.startswith("__external__/") or t.startswith("__unresolved__/") for t in targets)
    valid_targets = [t for t in targets if not (t.startswith("__external__/") or t.startswith("__unresolved__/"))]

    if not valid_targets and has_external:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="EXTERNAL_TARGET")

    # Query snapshot root directory
    async with db.execute("SELECT local_path FROM repo_snapshots WHERE id=?", (snapshot_id,)) as cur:
        snap_row = await cur.fetchone()

    if not snap_row or not snap_row["local_path"]:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="FILE_UNREADABLE")

    snap_root = Path(snap_row["local_path"])

    # 1-hop BFS from valid_targets over code_flow_edges
    expanded_paths: list[str] = list(valid_targets)
    seen_paths: set[str] = set(valid_targets)

    async with db.execute(
        "SELECT id, rel_path FROM code_flow_nodes WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        cf_nodes = await cur.fetchall()

    id_to_path = {r["id"]: r["rel_path"] for r in cf_nodes if r["rel_path"]}
    path_to_id = {r["rel_path"]: r["id"] for r in cf_nodes if r["rel_path"]}

    prefix = f"cfnode:{snapshot_id}:"
    seed_node_ids = {path_to_id.get(p, f"{prefix}{p}") for p in valid_targets}

    async with db.execute(
        "SELECT src_node_id, dst_node_id, edge_kind FROM code_flow_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        edge_rows = await cur.fetchall()

    one_hop_neighbors: list[str] = []
    for er in edge_rows:
        e_kind = er["edge_kind"]
        if e_kind not in _ALLOWED_BFS_EDGE_TYPES:
            continue
        src_id = er["src_node_id"]
        dst_id = er["dst_node_id"]

        src_path = id_to_path.get(src_id) or (src_id[len(prefix):] if src_id.startswith(prefix) else None)
        dst_path = id_to_path.get(dst_id) or (dst_id[len(prefix):] if dst_id.startswith(prefix) else None)

        # Check both forward and backward edges
        if src_id in seed_node_ids and dst_path:
            if dst_path not in seen_paths and not (dst_path.startswith("__external__/") or dst_path.startswith("__unresolved__/")):
                seen_paths.add(dst_path)
                one_hop_neighbors.append(dst_path)

        if dst_id in seed_node_ids and src_path:
            if src_path not in seen_paths and not (src_path.startswith("__external__/") or src_path.startswith("__unresolved__/")):
                seen_paths.add(src_path)
                one_hop_neighbors.append(src_path)

    one_hop_neighbors.sort()
    expanded_paths.extend(one_hop_neighbors[:10])

    # Load candidate occurrences for seed + 1-hop paths
    seed_targets_set = set(valid_targets)
    candidate_occurrences: list[tuple[bool, EvidenceOccurrence]] = []
    seen_occ_ids: set[str] = set()

    for path in expanded_paths:
        is_seed = path in seed_targets_set
        occs = await load_evidence_for_target(db, snapshot_id, path)
        for occ in occs:
            if occ.id not in seen_occ_ids:
                seen_occ_ids.add(occ.id)
                candidate_occurrences.append((is_seed, occ))

    if not candidate_occurrences:
        if has_external:
            return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="EXTERNAL_TARGET")
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="NO_OCCURRENCE")

    # Sort candidates deterministically:
    # 1. Seed file occurrences first (0 for seed, 1 for 1-hop)
    # 2. Preferred kind (for branch: guard first; for step: edge/step first)
    # 3. Smallest span length (line_end - line_start)
    # 4. rel_path
    # 5. line_start
    # 6. occurrence id
    def _rank_expanded_candidate(item: tuple[bool, EvidenceOccurrence]) -> tuple[int, int, int, str, int, str]:
        is_seed, occ = item
        seed_priority = 0 if is_seed else 1
        if unit_kind == "branch":
            kind_priority = 0 if occ.kind == "guard" else 1
        else:
            kind_priority = 0 if occ.kind in ("edge", "step") else 1
        span_len = occ.line_end - occ.line_start
        return (seed_priority, kind_priority, span_len, occ.rel_path, occ.line_start, occ.id)

    candidate_occurrences.sort(key=_rank_expanded_candidate)

    # Cut snippets (max 6 snippets, total chars budget <= 8000)
    snippets: list[Snippet] = []
    total_chars = 0

    for is_seed, occ in candidate_occurrences:
        if len(snippets) >= 6:
            break

        file_path = snap_root / occ.rel_path
        if not file_path.is_file():
            continue

        try:
            raw_bytes = file_path.read_bytes()
        except Exception:
            continue

        source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        text_content = raw_bytes.decode("utf-8", errors="replace")
        lines = text_content.splitlines()
        total_lines = len(lines)

        if total_lines == 0:
            continue

        padded_start = max(1, occ.line_start - 10)
        padded_end = min(total_lines, occ.line_end + 10)

        # Hard cap 1: line count <= 80
        if padded_end - padded_start + 1 > 80:
            padded_end = min(total_lines, padded_start + 79)

        snippet_lines = lines[padded_start - 1 : padded_end]
        snippet_text = "\n".join(snippet_lines)

        # Hard cap 2: char length <= 3000 chars (truncate at line boundary)
        if len(snippet_text) > 3000:
            kept_lines: list[str] = []
            curr_len = 0
            truncated_end = padded_start

            for idx, line in enumerate(snippet_lines):
                line_len = len(line) + (1 if kept_lines else 0)
                if curr_len + line_len > 3000:
                    break
                kept_lines.append(line)
                curr_len += line_len
                truncated_end = padded_start + idx

            if kept_lines:
                kept_lines.append("... [truncated]")
                snippet_text = "\n".join(kept_lines)
                padded_end = truncated_end
            else:
                snippet_text = snippet_lines[0][:3000] + "\n... [truncated]"
                padded_end = padded_start

        # Check total chars budget (<= 8000)
        if snippets and (total_chars + len(snippet_text) > 8000):
            break

        snippets.append(
            Snippet(
                rel_path=occ.rel_path,
                line_start=padded_start,
                line_end=padded_end,
                text=snippet_text,
                source_sha256=source_sha256,
                occurrence_id=occ.id,
                parse_status=occ.parse_status,
            )
        )
        total_chars += len(snippet_text)

    if not snippets:
        return RetrievalResult(unit_id=unit_id, snippets=[], abstain_reason="FILE_UNREADABLE")

    # Deterministic ordering: sort by rel_path, line_start, line_end
    snippets.sort(key=lambda s: (s.rel_path, s.line_start, s.line_end))

    return RetrievalResult(unit_id=unit_id, snippets=snippets, abstain_reason=None)

