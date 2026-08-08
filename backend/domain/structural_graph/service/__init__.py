"""Structural graph service — package facade.

Split by responsibility from the former single-file `service.py`:
  - `_path_resolve`   : path normalization / import-target resolution
  - `_import_parsing` : source-level import statement extraction (Python/TS/JS)
  - `_scoring`         : node scoring & neighbor expansion (native + Python fallback)
  - `_build`           : the `build()` pipeline (extraction cache, edges, symbol graph)
  - `_communities`     : Louvain community detection and community reads
  - `_queries`          : read-only queries (summary, edges, neighbors, cycles, symbols)
  - `_export`           : full graph.json export

This module re-exports every name previously importable from `service.py` so
`from domain.structural_graph.service import X` keeps working unchanged.
"""
from __future__ import annotations

import os

from ._build import _SYMBOL_GRAPH_BUILDER_ENABLED, _BuildMixin
from ._communities import _CommunityMixin
from ._export import _ExportMixin
from ._import_parsing import (
    _RE_TS_IMPORT,
    _RE_TS_REQUIRE,
    _extract_python_imports,
    _extract_ts_js_imports,
)
from ._path_resolve import (
    _build_py_suffix_index,
    _is_entrypoint,
    _is_init_file,
    _normalize,
    _resolve_relative_import,
)
from ._queries import _QueryMixin
from ._scoring import _compute_scores_python, _expand_neighbors_python, _load_native_graph

__all__ = [
    "StructuralGraphService",
    "_SYMBOL_GRAPH_BUILDER_ENABLED",
    "_RE_TS_IMPORT",
    "_RE_TS_REQUIRE",
    "_load_native_graph",
    "_compute_scores_python",
    "_expand_neighbors_python",
    "_normalize",
    "_is_init_file",
    "_build_py_suffix_index",
    "_is_entrypoint",
    "_resolve_relative_import",
    "_extract_python_imports",
    "_extract_ts_js_imports",
]


class StructuralGraphService(_BuildMixin, _CommunityMixin, _QueryMixin, _ExportMixin):
    def __init__(self):
        self._data_dir = os.getenv("CODESPECTRA_DATA_DIR", ".")
