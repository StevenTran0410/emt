"""Main ASTChunker orchestration class."""
from __future__ import annotations

import logging
from typing import Any

from ._flat import _flat_chunks
from ._lang_configs import LANGUAGE_CONFIGS
from ._nodes import _collect_nodes
from ._parser import _get_parser
from ._prepasses import _PRE_PASSES
from ._types import ASTChunk

logger = logging.getLogger(__name__)


class ASTChunker:
    """
    Produces semantically coherent code chunks from source text.

    Usage:
        chunker = ASTChunker()
        chunks = chunker.chunk(source, language="python", target_size=1500)
    """

    # Maximum size of a single node before we recursively split its children.
    _MAX_NODE_SIZE = 2000

    def chunk(
        self,
        source: str,
        language: str,
        target_size: int = 1500,
    ) -> list[ASTChunk]:
        """
        Return AST-based chunks for supported languages.
        Falls back to flat chunking for unsupported languages or on any error.
        Applies pre-passes (license stripping, barrel detection) before chunking.
        """
        lang_key = language.lower() if language else ""

        # Apply pre-passes.
        pre_pass_chunk_type_override = None
        for pre_pass in _PRE_PASSES:
            try:
                source, override = pre_pass(source, lang_key)
                if override:
                    pre_pass_chunk_type_override = override
            except Exception as exc:
                logger.warning("[chunker_ast] pre-pass failed: %s", exc)

        # If barrel was detected, return single chunk with barrel type.
        if pre_pass_chunk_type_override == "barrel":
            line_count = source.count("\n") if source else 0
            return [ASTChunk(
                text=source or "[barrel file — re-exports only]",
                chunk_type="barrel",
                start_line=0,
                end_line=line_count,
                language=lang_key,
            )]

        if lang_key not in LANGUAGE_CONFIGS:
            return _flat_chunks(source, target_size)

        # Short-circuit: file fits in a single chunk — skip AST overhead.
        if len(source) <= target_size:
            line_count = source.count("\n")
            return [ASTChunk(
                text=source,
                chunk_type="file",
                start_line=0,
                end_line=line_count,
                language=lang_key,
            )]

        parser = _get_parser(lang_key)
        if parser is None:
            return _flat_chunks(source, target_size)

        try:
            return self._parse_and_chunk(source, lang_key, parser, target_size)
        except Exception as exc:
            logger.warning(
                "[chunker_ast] parse/chunk failed for language=%s, falling back to flat: %s",
                lang_key, exc,
            )
            return _flat_chunks(source, target_size)

    def _parse_and_chunk(
        self,
        source: str,
        language: str,
        parser: Any,
        target_size: int,
    ) -> list[ASTChunk]:
        # Local import: _merge_spans reads the package-level `_native_chunker`
        # global by bare name, and tests monkeypatch that global via
        # `chunker_ast._native_chunker = None`. Resolving it here (at call
        # time) rather than at module load time ensures the patched value is
        # observed.
        from . import _merge_spans

        cfg = LANGUAGE_CONFIGS[language]
        src_bytes = source.encode("utf-8", errors="replace")
        tree = parser.parse(src_bytes)
        root = tree.root_node

        spans = _collect_nodes(root, src_bytes, cfg, self._MAX_NODE_SIZE)

        if not spans:
            # No semantic nodes found (e.g. script with only top-level expressions).
            return _flat_chunks(source, target_size)

        groups = _merge_spans(spans, target_size)

        chunks: list[ASTChunk] = []
        for group in groups:
            first = group[0]
            last = group[-1]
            text = src_bytes[first.start_byte:last.end_byte].decode("utf-8", errors="replace")

            # Determine dominant chunk_type for the group.
            types_in_group = [s.node_type for s in group]
            if all(t == "import_group" for t in types_in_group):
                chunk_type = "import_group"
            elif len(group) == 1:
                t = group[0].node_type
                if "function" in t or "method" in t:
                    chunk_type = "function"
                elif "class" in t or "struct" in t or "interface" in t:
                    chunk_type = "class"
                else:
                    chunk_type = "block"
            else:
                chunk_type = "block"

            names = [s.name for s in group if s.name]
            chunks.append(ASTChunk(
                text=text,
                chunk_type=chunk_type,
                node_names=names,
                start_line=first.start_line,
                end_line=last.end_line,
                language=language,
                split_part=first.split_part,
                split_of=first.split_of,
            ))

        return chunks
