"""JCL resolution engine for EXEC program execution and DD dataset binding."""
from __future__ import annotations

import urllib.parse
from typing import NamedTuple

from .extract import JclDdFact, JclExecFact

STANDARD_EXTERNAL_UTILITIES = {
    "IDCAMS",
    "SORT",
    "IEFBR14",
    "ICEGENER",
    "IEBGENER",
    "IEBCOPY",
    "IKJEFT01",
    "AMBLIST",
    "DFSRRC00",
}


class ResolvedJclEdge(NamedTuple):
    src_file: str
    dst_file: str
    src_symbol: str
    dst_symbol: str | None
    edge_type: str  # "executes" or "binds_dataset"
    is_external: bool
    confidence_score: float
    resolution_method: str
    evidence_lines: list[int]


def resolve_jcl_execs(
    src_file: str,
    execs: list[JclExecFact],
    program_index: dict[str, list[str]],
) -> list[ResolvedJclEdge]:
    """Resolve JCL EXEC PGM= facts into edges."""
    edges: list[ResolvedJclEdge] = []

    for e in execs:
        job_prefix = f"{e.job_name}." if e.job_name else ""
        src_symbol = f"{src_file}::{job_prefix}{e.step_name}"
        pgm = e.program_name.upper()

        if pgm.startswith("&"):
            dst_file = f"__unresolved__/program/{urllib.parse.quote(pgm, safe='')}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="executes",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="symbolic_program",
                    evidence_lines=sorted(e.evidence_lines),
                )
            )
            continue

        matches = program_index.get(pgm, [])
        if len(matches) == 1:
            dst_file = matches[0]
            dst_symbol = f"{dst_file}::{pgm}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=dst_symbol,
                    edge_type="executes",
                    is_external=False,
                    confidence_score=1.0,
                    resolution_method="jcl_exec_statement",
                    evidence_lines=sorted(e.evidence_lines),
                )
            )
        elif pgm in STANDARD_EXTERNAL_UTILITIES or not matches:
            dst_file = f"__external__/program/{pgm}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="executes",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="external_utility"
                    if pgm in STANDARD_EXTERNAL_UTILITIES
                    else "program_not_in_snapshot",
                    evidence_lines=sorted(e.evidence_lines),
                )
            )
        else:
            dst_file = f"__external__/program/{pgm}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="executes",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="ambiguous_program_id",
                    evidence_lines=sorted(e.evidence_lines),
                )
            )

    return edges


def resolve_jcl_dds(
    src_file: str,
    dds: list[JclDdFact],
) -> list[ResolvedJclEdge]:
    """Resolve JCL DD DSN= facts into synthetic dataset edges."""
    edges: list[ResolvedJclEdge] = []

    for d in dds:
        job_prefix = f"{d.job_name}." if d.job_name else ""
        src_symbol = f"{src_file}::{job_prefix}{d.step_name}.DD.{d.dd_name}"
        dsn = d.dsn.strip()

        if dsn.startswith("&"):
            encoded = urllib.parse.quote(dsn, safe="")
            dst_file = f"__unresolved__/dataset/{encoded}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="binds_dataset",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="symbolic_dsn",
                    evidence_lines=sorted(d.evidence_lines),
                )
            )
        elif dsn.startswith("*."):
            encoded = urllib.parse.quote(dsn, safe="")
            dst_file = f"__unresolved__/dataset/{encoded}"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=None,
                    edge_type="binds_dataset",
                    is_external=True,
                    confidence_score=0.0,
                    resolution_method="jcl_back_reference",
                    evidence_lines=sorted(d.evidence_lines),
                )
            )
        else:
            # Percent-encode any character outside the valid MVS DSN set so the
            # synthetic node id stays a clean, unambiguous key.
            safe_dsn = urllib.parse.quote(dsn, safe=".@#$-()+")
            dst_file = f"__synthetic__/dataset/{safe_dsn}"
            dst_symbol = f"{dst_file}::DATASET"
            edges.append(
                ResolvedJclEdge(
                    src_file=src_file,
                    dst_file=dst_file,
                    src_symbol=src_symbol,
                    dst_symbol=dst_symbol,
                    edge_type="binds_dataset",
                    is_external=False,
                    confidence_score=1.0,
                    resolution_method="jcl_dd_statement",
                    evidence_lines=sorted(d.evidence_lines),
                )
            )

    return edges
