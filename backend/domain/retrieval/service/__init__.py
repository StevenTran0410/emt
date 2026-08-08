"""Retrieval service: chunking + lexical + hybrid/vectorless retrieval."""

from __future__ import annotations

from ._build import _IndexBuildMixin
from ._chunking import _chunk_size_for, _ends_mid_function, _token_estimate
from ._constants import _CHUNK_FULL_COLS, _SECTION_CATEGORY_HINTS
from ._query import _QueryMixin


class RetrievalService(_QueryMixin, _IndexBuildMixin):
    pass


__all__ = [
    "RetrievalService",
    "_chunk_size_for",
    "_ends_mid_function",
    "_token_estimate",
    "_CHUNK_FULL_COLS",
    "_SECTION_CATEGORY_HINTS",
]
