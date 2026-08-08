"""COBOL resolution engine for program CALLs and copybook COPYs."""
from __future__ import annotations

from typing import NamedTuple


class ResolvedCobolEdge(NamedTuple):
    src_file: str
    dst_file: str
    src_symbol: str
    dst_symbol: str | None
    edge_type: str  # "calls" or "copies"
    is_external: bool
    confidence_score: float
    resolution_method: str
    evidence_lines: list[int]


def build_program_index(cobol_files: dict[str, str | None]) -> dict[str, list[str]]:
    """Build map from uppercase real PROGRAM-ID to list of rel_paths.

    cobol_files maps rel_path -> extracted program_id (or None). Files without a
    real PROGRAM-ID (copybooks, unparseable fragments) are NOT indexed as program
    targets — using a filename-stem fallback would let a copybook masquerade as a
    program and create false local CALL/EXEC edges.
    """
    prog_map: dict[str, list[str]] = {}
    for rel_path, extracted_id in cobol_files.items():
        if not extracted_id:
            continue
        prog_map.setdefault(extracted_id.upper(), []).append(rel_path)
    return prog_map


def build_copybook_index(file_set: set[str]) -> dict[str, list[str]]:
    """Build map from uppercase copybook stem to list of rel_paths."""
    copy_map: dict[str, list[str]] = {}
    for rel_path in file_set:
        lower = rel_path.lower()
        if lower.endswith(".cpy"):
            stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
            copy_map.setdefault(stem, []).append(rel_path)
    return copy_map


def resolve_cobol_calls(
    src_file: str,
    src_program_id: str | None,
    calls: list[dict],  # list of {"callee": ..., "line": ..., "resolution_method": ...}
    program_index: dict[str, list[str]],
) -> list[ResolvedCobolEdge]:
    """Resolve CALL statements into edges."""
    src_symbol = f"{src_file}::{src_program_id}" if src_program_id else f"{src_file}::PROGRAM"

    # Group evidence lines by callee + resolution_method
    call_groups: dict[tuple[str, str], list[int]] = {}
    for c in calls:
        key = (c["callee"].upper(), c["resolution_method"])
        call_groups.setdefault(key, []).append(c["line"])

    edges: list[ResolvedCobolEdge] = []

    for (callee, res_method), lines in call_groups.items():
        # Dynamic CALL <data-name>: the target is a runtime value, not a literal
        # program name. It must never be resolved against the program index (that
        # would be a guess). Always emit as external.
        if res_method == "dynamic_call":
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=f"__external__/program/{callee}",
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="calls",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="dynamic_call",
                    evidence_lines=sorted(lines),
                )
            )
            continue

        matches = program_index.get(callee, [])
        if len(matches) == 1:
            dst_file = matches[0]
            # Get dst program ID from dst_file if available, else callee
            dst_symbol = f"{dst_file}::{callee}"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=dst_symbol,
                    edge_type="calls",
                    is_external=False,
                    confidence_score=1.0,
                    resolution_method=res_method,
                    evidence_lines=sorted(lines),
                )
            )
        elif len(matches) > 1:
            # Ambiguous program ID
            dst_file = f"__external__/program/{callee}"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="calls",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="ambiguous_program_id",
                    evidence_lines=sorted(lines),
                )
            )
        else:
            # Missing / external program
            dst_file = f"__external__/program/{callee}"
            res_m = "dynamic_call" if res_method == "dynamic_call" else "program_not_in_snapshot"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="calls",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method=res_m,
                    evidence_lines=sorted(lines),
                )
            )

    return edges


def resolve_cobol_copies(
    src_file: str,
    src_program_id: str | None,
    copies: list[dict],  # list of {"member": ..., "line": ..., "resolution_method": ...}
    copybook_index: dict[str, list[str]],
) -> list[ResolvedCobolEdge]:
    """Resolve COPY statements into edges."""
    src_symbol = f"{src_file}::{src_program_id}" if src_program_id else f"{src_file}::MEMBER"

    copy_groups: dict[str, list[int]] = {}
    for c in copies:
        member = c["member"].upper()
        copy_groups.setdefault(member, []).append(c["line"])

    edges: list[ResolvedCobolEdge] = []

    for member, lines in copy_groups.items():
        matches = copybook_index.get(member, [])
        if len(matches) == 1:
            dst_file = matches[0]
            dst_symbol = f"{dst_file}::{member}"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=dst_symbol,
                    edge_type="copies",
                    is_external=False,
                    confidence_score=1.0,
                    resolution_method="cobol_copy_statement",
                    evidence_lines=sorted(lines),
                )
            )
        elif len(matches) > 1:
            dst_file = f"__unresolved__/copybook/{member}"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="copies",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="ambiguous_copybook",
                    evidence_lines=sorted(lines),
                )
            )
        else:
            dst_file = f"__unresolved__/copybook/{member}"
            edges.append(
                ResolvedCobolEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="copies",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="copybook_not_found",
                    evidence_lines=sorted(lines),
                )
            )

    return edges
