"""COBOL adapter package for CodeSpectra structural graph."""

from .extract import (
    CobolCallFact,
    CobolCopyFact,
    CobolExtractionResult,
    extract_cobol_all,
    extract_cobol_facts,
)
from .preprocess import is_copybook_shaped, normalize_cobol_text
from .resolve import (
    ResolvedCobolEdge,
    build_copybook_index,
    build_program_index,
    resolve_cobol_calls,
    resolve_cobol_copies,
)

__all__ = [
    "normalize_cobol_text",
    "is_copybook_shaped",
    "extract_cobol_facts",
    "extract_cobol_all",
    "CobolCallFact",
    "CobolCopyFact",
    "CobolExtractionResult",
    "ResolvedCobolEdge",
    "build_program_index",
    "build_copybook_index",
    "resolve_cobol_calls",
    "resolve_cobol_copies",
]
