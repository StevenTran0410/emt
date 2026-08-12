import re
import urllib.parse
from typing import Any, NamedTuple

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


def build_proc_index(file_set: set[str]) -> dict[str, list[str]]:
    """Build map from uppercase proc stem to list of rel_paths (.prc files only)."""
    proc_map: dict[str, list[str]] = {}
    for rel_path in file_set:
        lower = rel_path.lower()
        if lower.endswith(".prc"):
            stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()
            proc_map.setdefault(stem, []).append(rel_path)
    return proc_map


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
    proc_index: dict[str, list[str]] | None = None,
) -> list[ResolvedJclEdge]:
    """Resolve JCL EXEC facts strictly into program or proc execution edges."""
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

        if getattr(e, "is_proc", False):
            # Strict PROC resolution — .prc files only
            proc_matches = proc_index.get(pgm, []) if proc_index else []
            if len(proc_matches) == 1:
                dst_file = proc_matches[0]
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
                        resolution_method="jcl_exec_proc",
                        evidence_lines=sorted(e.evidence_lines),
                    )
                )
            elif len(proc_matches) > 1:
                dst_file = f"__external__/proc/{pgm}"
                edges.append(
                    ResolvedJclEdge(
                        src_file=src_file,
                        dst_file=dst_file,
                        src_symbol=src_symbol,
                        dst_symbol=None,
                        edge_type="executes",
                        is_external=True,
                        confidence_score=0.0,
                        resolution_method="ambiguous_proc",
                        evidence_lines=sorted(e.evidence_lines),
                    )
                )
            else:
                dst_file = f"__external__/proc/{pgm}"
                edges.append(
                    ResolvedJclEdge(
                        src_file=src_file,
                        dst_file=dst_file,
                        src_symbol=src_symbol,
                        dst_symbol=None,
                        edge_type="executes",
                        is_external=True,
                        confidence_score=0.0,
                        resolution_method="proc_not_in_snapshot",
                        evidence_lines=sorted(e.evidence_lines),
                    )
                )
            continue

        # Program resolution (PGM=)
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


def resolve_jcl_stacks(
    src_file: str,
    sidecar_facts: list[dict[str, Any]],
    jcl_index: dict[str, list[str]],
) -> list[ResolvedJclEdge]:
    """Resolve JCL STACK facts into structural 'stacks' edges linking to stacked .jcl target."""
    edges: list[ResolvedJclEdge] = []
    for fact in sidecar_facts:
        if fact.get("fact_type") != "stack":
            continue
        attrs = fact.get("attributes") or {}
        member = attrs.get("member")
        if not member:
            val = str(fact.get("value") or "")
            m = re.search(r"\bMEMBER=([A-Za-z0-9#$@]+)", val, re.IGNORECASE)
            if m:
                member = m.group(1)
            elif val and "=" not in val:
                member = val.strip()

        if not member:
            continue

        member_stem = member.strip().upper()
        matches = jcl_index.get(member_stem, [])
        if len(matches) == 1:
            dst_file = matches[0]
            is_external = False
            confidence = 1.0
            dst_symbol: str | None = f"{dst_file}::{member_stem}"
        else:
            dst_file = f"__external__/jcl/{member_stem}"
            is_external = True
            confidence = 0.0
            dst_symbol = None

        line_no = fact.get("line_start") or 1
        edges.append(
            ResolvedJclEdge(
                src_file=src_file,
                dst_file=dst_file,
                src_symbol=f"{src_file}::stack",
                dst_symbol=dst_symbol,
                edge_type="stacks",
                is_external=is_external,
                confidence_score=confidence,
                resolution_method="jcl_stack",
                evidence_lines=[line_no],
            )
        )
    return edges
