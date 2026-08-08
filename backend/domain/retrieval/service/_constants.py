"""Shared constants and module-level singletons for the retrieval service."""

from __future__ import annotations

import re

from ..chunker_ast import ASTChunker
from ..types import RetrievalSection

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[A-Za-z0-9_]+")

# Languages that get AST-based semantic chunking.
_AST_LANGS: frozenset[str] = frozenset(
    {
        "python",
        "typescript",
        "javascript",
        "cpp",
        "go",
        "java",
        "c",
        "rust",
        "ruby",
        "php",
        "csharp",
        "kotlin",
        "scala",
        "bash",
        "sh",
        "lua",
        "zig",
        "haskell",
        "elixir",
        "ocaml",
        "julia",
        "yaml",
        "toml",
        "html",
        "css",
        "json",
        "markdown",
        "groovy",
        "cmake",
        "svelte",
        "sql",
    }
)

# Module-level singleton — parser/Language objects are cached inside.
_ast_chunker = ASTChunker()

_SECTION_BUDGETS: dict[RetrievalSection, int] = {
    # Budget = tokens reserved for evidence only; system prompt + user preamble add ~800 more.
    RetrievalSection.ARCHITECTURE: 14_000,
    RetrievalSection.CONVENTIONS: 10_000,
    RetrievalSection.FEATURE_MAP: 14_000,
    RetrievalSection.IMPORTANT_FILES: 12_000,
    RetrievalSection.GLOSSARY: 7_000,
    RetrievalSection.QA: 12_000,
}

_SECTION_CATEGORY_HINTS: dict[RetrievalSection, set[str]] = {
    RetrievalSection.ARCHITECTURE: {"source", "config", "infra"},
    RetrievalSection.CONVENTIONS: {"source", "test", "config"},
    RetrievalSection.FEATURE_MAP: {"source", "docs"},
    RetrievalSection.IMPORTANT_FILES: {"source", "config", "infra"},
    RetrievalSection.GLOSSARY: {"source", "docs"},
    RetrievalSection.QA: {"source", "config", "docs"},
}


# Avoids loading `content` when only metadata is needed, saving bandwidth on large codebases.
_CHUNK_FULL_COLS = "id, snapshot_id, rel_path, language, category, chunk_index, content, token_estimate, chunk_type, start_line, end_line, split_part, split_of"


_BRACE_LANGS = {
    "javascript",
    "typescript",
    "java",
    "cpp",
    "c",
    "go",
    "rust",
    "csharp",
    "kotlin",
    "scala",
    "groovy",
    "php",
    "zig",
    "dart",
    "swift",
}
