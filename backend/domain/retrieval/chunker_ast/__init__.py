"""AST-based semantic chunker for code files.

Replaces flat line-count chunking with syntax-aware chunking via Tree-sitter.
Functions, classes, and other logical units are never split mid-body.

Supported languages: python, typescript, javascript, cpp, go, java.
Falls back to flat chunking for unsupported languages or on any parse error.

Package facade — re-exports the original module's public and private names so
`from domain.retrieval.chunker_ast import <name>` keeps working unchanged.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

from ._types import ASTChunk, _NodeSpan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Native merge-pass hotspot (optional — graceful fallback to Python)
#
# Kept in this facade module (not a submodule) because tests monkeypatch
# `_native_chunker` via `chunker_ast._native_chunker = None` and expect
# `_merge_spans` below (which reads the bare name) to observe the change; a
# submodule-local copy of the name would not see the patched value.
# ---------------------------------------------------------------------------

def _load_native_chunker() -> Any:
    try:
        return importlib.import_module("domain.retrieval._native_chunker")
    except Exception:
        return None


_native_chunker = _load_native_chunker()

# ---------------------------------------------------------------------------
# Merge pass
# ---------------------------------------------------------------------------

def _merge_spans_python(spans: list[_NodeSpan], target_size: int) -> list[list[_NodeSpan]]:
    """
    Pure Python fallback merge: greedy accumulation.

    Rules:
    - Import group spans are never merged with non-import spans (imports keep
      their own group to preserve chunk_type='import_group' semantics).
    - Non-import spans are greedily accumulated while their byte range fits
      within target_size.
    """
    if not spans:
        return []
    groups: list[list[_NodeSpan]] = []
    current: list[_NodeSpan] = []
    group_start = 0

    def flush() -> None:
        if current:
            groups.append(list(current))
            current.clear()

    for span in spans:
        if span.is_import:
            # Imports always get their own group — flush any in-progress group first.
            flush()
            groups.append([span])
            continue
        # Non-import span: attempt to merge into current group.
        if not current:
            current.append(span)
            group_start = span.start_byte
        else:
            candidate_len = span.end_byte - group_start
            if candidate_len > target_size:
                flush()
                current.append(span)
                group_start = span.start_byte
            else:
                current.append(span)

    flush()
    return groups


def _merge_spans(spans: list[_NodeSpan], target_size: int) -> list[list[_NodeSpan]]:
    """
    Merge spans into groups.

    Import spans and split-oversized-node parts (split_of > 1) are always emitted
    as their own group -- merging split parts back together would recreate the
    overflow the split exists to avoid. Everything else is merged greedily up to
    target_size, using the native C++ hotspot when available.
    """
    if not spans:
        return []

    # Split the span list at import/split-part boundaries, preserving order.
    segments: list[list[_NodeSpan]] = []
    current_seg: list[_NodeSpan] = []

    for span in spans:
        if span.is_import or span.split_of > 1:
            if current_seg:
                segments.append(current_seg)
                current_seg = []
            segments.append([span])  # always its own segment
        else:
            current_seg.append(span)

    if current_seg:
        segments.append(current_seg)

    result: list[list[_NodeSpan]] = []
    for seg in segments:
        if not seg:
            continue
        if seg[0].is_import or seg[0].split_of > 1:
            # Import or split-part segment: emit as-is, never merged.
            result.append(seg)
            continue
        # Non-import segment: merge with native or Python fallback.
        if _native_chunker is not None:
            try:
                # Build local index within this segment.
                triples = [(s.start_byte, s.end_byte, i) for i, s in enumerate(seg)]
                raw_groups = _native_chunker.merge_spans(triples, target_size)
                for group_indices in raw_groups:
                    result.append([seg[idx] for idx in group_indices])
                continue
            except Exception as exc:
                logger.debug(
                    "[chunker_ast] native merge_spans failed, using Python fallback: %s", exc
                )
        # Python fallback for this segment.
        result.extend(_merge_spans_python(seg, target_size))

    return result


# ---------------------------------------------------------------------------
# Re-exports (backward compatibility — the original module's public surface)
# ---------------------------------------------------------------------------

from ._chunker import ASTChunker
from ._flat import _flat_chunks
from ._lang_configs import LANGUAGE_CONFIGS, LanguageConfig
from ._lang_factories import (
    _make_bash_lang,
    _make_c_lang,
    _make_cmake_lang,
    _make_cpp_lang,
    _make_csharp_lang,
    _make_css_lang,
    _make_elixir_lang,
    _make_go_lang,
    _make_groovy_lang,
    _make_haskell_lang,
    _make_html_lang,
    _make_java_lang,
    _make_javascript_lang,
    _make_json_lang,
    _make_julia_lang,
    _make_kotlin_lang,
    _make_lua_lang,
    _make_markdown_lang,
    _make_ocaml_lang,
    _make_php_lang,
    _make_python_lang,
    _make_ruby_lang,
    _make_rust_lang,
    _make_scala_lang,
    _make_sql_lang,
    _make_svelte_lang,
    _make_toml_lang,
    _make_typescript_lang,
    _make_yaml_lang,
    _make_zig_lang,
)
from ._nodes import _collect_nodes, _extract_name, _split_oversized_span
from ._parser import _get_parser, _parser_cache
from ._prepasses import _PRE_PASSES, _detect_barrel_pass, _strip_license_header_pass

__all__ = [
    "ASTChunk",
    "ASTChunker",
    "LanguageConfig",
    "LANGUAGE_CONFIGS",
]
