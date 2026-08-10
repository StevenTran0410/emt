"""JCL adapter package for CodeSpectra structural graph."""

from .extract import JclDdFact, JclExecFact, JclExtractionResult, extract_jcl_facts
from .resolve import (
    ResolvedJclEdge,
    build_proc_index,
    resolve_jcl_dds,
    resolve_jcl_execs,
)

__all__ = [
    "extract_jcl_facts",
    "JclExecFact",
    "JclDdFact",
    "JclExtractionResult",
    "ResolvedJclEdge",
    "resolve_jcl_execs",
    "resolve_jcl_dds",
    "build_proc_index",
]
