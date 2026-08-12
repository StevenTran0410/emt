"""ISPF/TSO resolution engine: menu_option (pfd->clist), calls (clist->cbl
program), submits (clist->jcl). Mirrors _jcl/resolve.py's basename-stem
index-and-route pattern, routing misses to synthetic __external__ nodes."""
from __future__ import annotations

from typing import NamedTuple

from .extract import ClistCallFact, ClistSubmitFact, PfdMenuOptionFact


class ResolvedIspfEdge(NamedTuple):
    src_file: str
    dst_file: str
    src_symbol: str
    dst_symbol: str | None
    edge_type: str  # "menu_option", "calls", or "submits"
    is_external: bool
    confidence_score: float
    resolution_method: str
    evidence_lines: list[int]


def build_ext_index(file_set: set[str], ext: str) -> dict[str, list[str]]:
    """Map uppercase basename stem -> rel_paths for files ending in ext (e.g. '.clist')."""
    idx: dict[str, list[str]] = {}
    for rel_path in file_set:
        if rel_path.lower().endswith(ext):
            stem = rel_path.rsplit("/", 1)[-1][: -len(ext)].upper()
            idx.setdefault(stem, []).append(rel_path)
    return idx


def _resolve(
    src_file: str,
    src_symbol: str,
    target: str,
    line: int,
    index: dict[str, list[str]],
    edge_type: str,
    external_kind: str,
    resolution_method: str,
) -> ResolvedIspfEdge:
    matches = index.get(target.upper(), [])
    if len(matches) == 1:
        dst_file = matches[0]
        return ResolvedIspfEdge(
            src_file=src_file,
            dst_file=dst_file,
            src_symbol=src_symbol,
            dst_symbol=f"{dst_file}::{target.upper()}",
            edge_type=edge_type,
            is_external=False,
            confidence_score=1.0,
            resolution_method=resolution_method,
            evidence_lines=[line],
        )
    method = "ambiguous_target" if matches else f"{external_kind}_not_in_snapshot"
    return ResolvedIspfEdge(
        src_file=src_file,
        dst_file=f"__external__/{external_kind}/{target.upper()}",
        src_symbol=src_symbol,
        dst_symbol=None,
        edge_type=edge_type,
        is_external=True,
        confidence_score=0.0,
        resolution_method=method,
        evidence_lines=[line],
    )


def resolve_menu_options(
    src_file: str, options: list[PfdMenuOptionFact], clist_index: dict[str, list[str]]
) -> list[ResolvedIspfEdge]:
    """Resolve active (non-disabled) )PROC CMD(<target>) dispatches to .clist files."""
    edges: list[ResolvedIspfEdge] = []
    for opt in options:
        if opt.disabled:
            continue
        src_symbol = f"{src_file}::OPT{opt.option}"
        edges.append(
            _resolve(
                src_file, src_symbol, opt.target, opt.line, clist_index,
                "menu_option", "clist", "pfd_cmd_dispatch",
            )
        )
    return edges


def resolve_clist_calls(
    src_file: str, calls: list[ClistCallFact], program_index: dict[str, list[str]]
) -> list[ResolvedIspfEdge]:
    """Resolve CLIST CALL '<lib>(<member>)' targets to .cbl program files."""
    edges: list[ResolvedIspfEdge] = []
    for c in calls:
        src_symbol = f"{src_file}::PROC"
        edges.append(
            _resolve(
                src_file, src_symbol, c.member, c.line, program_index,
                "calls", "program", "clist_call_statement",
            )
        )
    return edges


def resolve_cobol_panel_refs(
    src_file: str, content: str, ipf_index: dict[str, list[str]]
) -> list[ResolvedIspfEdge]:
    """Emit a deterministic program->panel edge per .ipf stem quoted as a literal
    in the COBOL source (e.g. 'MIA-MEMBER PIC X(8) VALUE 'FHNIXLOT''). Ambiguous
    stems (matching >1 .ipf file) are skipped rather than guessed."""
    edges: list[ResolvedIspfEdge] = []
    src_symbol = f"{src_file}::PROGRAM"
    lines: list[str] | None = None
    for stem, matches in ipf_index.items():
        if len(matches) != 1:
            continue
        needle = f"'{stem}'"
        if needle not in content:
            continue
        if lines is None:
            lines = content.splitlines()
        line_no = next((i for i, l in enumerate(lines, 1) if needle in l), 1)
        dst_file = matches[0]
        edges.append(
            ResolvedIspfEdge(
                src_file=src_file,
                dst_file=dst_file,
                src_symbol=src_symbol,
                dst_symbol=f"{dst_file}::{stem}",
                edge_type="shows_panel",
                is_external=False,
                confidence_score=1.0,
                resolution_method="cobol_quoted_panel_literal",
                evidence_lines=[line_no],
            )
        )
    return edges


def resolve_clist_submits(
    src_file: str, submits: list[ClistSubmitFact], jcl_index: dict[str, list[str]]
) -> list[ResolvedIspfEdge]:
    """Resolve CLIST SUBMIT '<lib>(<member>)' targets to .jcl files."""
    edges: list[ResolvedIspfEdge] = []
    for s in submits:
        src_symbol = f"{src_file}::PROC"
        edges.append(
            _resolve(
                src_file, src_symbol, s.member, s.line, jcl_index,
                "submits", "jcl", "clist_submit_statement",
            )
        )
    return edges
