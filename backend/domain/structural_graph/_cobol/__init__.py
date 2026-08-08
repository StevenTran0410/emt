"""COBOL adapter package for CodeSpectra structural graph."""

from .extract import CobolCallFact, CobolCopyFact, CobolExtractionResult, extract_cobol_facts
from .preprocess import normalize_cobol_text
from .resolve import (
    ResolvedCobolEdge,
    build_copybook_index,
    build_program_index,
    resolve_cobol_calls,
    resolve_cobol_copies,
)

__all__ = [
    "normalize_cobol_text",
    "extract_cobol_facts",
    "CobolCallFact",
    "CobolCopyFact",
    "CobolExtractionResult",
    "ResolvedCobolEdge",
    "build_program_index",
    "build_copybook_index",
    "resolve_cobol_calls",
    "resolve_cobol_copies",
]
