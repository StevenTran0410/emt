"""Symbol parser.

Extracts symbol definitions, imports, call sites, and attribute assignments
from Python and TypeScript source files.

All parsing is syntax-only; no type inference is performed here.

This package is split by responsibility:
    - ``_models``:     Shared dataclasses (ImportInfo, SymbolInfo, etc.)
    - ``_python``:      Python parser (ast module).
    - ``_typescript``:  TypeScript/JavaScript parser (tree-sitter).

All names below are re-exported here to preserve the original flat
``_symbol_parser`` module's public surface.
"""
from __future__ import annotations

import logging

from ._models import AttributeAssign, CallSite, ImportInfo, ParsedFile, SymbolInfo
from ._python import (
    _ast_call_name,
    _ast_expr_str,
    _ast_name,
    _module_to_file,
    _parse_python,
    _PythonVisitor,
)
from ._typescript import (
    _parse_ts_calls,
    _parse_ts_import,
    _parse_typescript,
    _ts_module_to_file,
    _ts_walk,
)

__all__ = [
    "ImportInfo",
    "SymbolInfo",
    "AttributeAssign",
    "CallSite",
    "ParsedFile",
    "parse_file",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file(filename: str, source: str) -> ParsedFile | None:
    """Parse a source file and return extracted symbol information.

    Args:
        filename: File path (used for FQN construction and language detection).
        source:   Full source text.

    Returns:
        A :class:`ParsedFile`, or ``None`` if the file could not be parsed.
    """
    try:
        lower = filename.lower()
        if lower.endswith(".py"):
            return _parse_python(filename, source)
        if lower.endswith((".ts", ".tsx", ".js", ".jsx")):
            lang_name = "javascript" if lower.endswith((".js", ".jsx")) else "typescript"
            return _parse_typescript(filename, source, lang_name=lang_name)
        return None
    except Exception:
        logger.exception("parse_file failed for %s", filename)
        return None
