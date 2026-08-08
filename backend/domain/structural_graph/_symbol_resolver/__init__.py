"""Symbol resolver.

Resolves call sites to concrete SymbolEdge objects using import namespace
tracking, constructor-assignment analysis, and MRO-style inheritance walking.

Confidence levels:
  CONF_HIGH  — exactly one unambiguous resolution path
  CONF_LOW   — multiple candidates or interface-typed receiver
  CONF_NONE  — unresolvable; caller MUST NOT emit an edge for these

Resolution method mapping:
  Each SymbolEdge is assigned a confidence_score (0.0-1.0 float) and a
  resolution_method label indicating how the resolution succeeded:

  - 'import_path_match': bare call resolved via import namespace to a module
    function (confidence_score=0.95). Exact match: import says where to find it.
  - 'same_file_scope': bare call found in same file, single candidate
    (confidence_score=0.85). Fairly certain: no ambiguity in local scope.
  - 'mro_resolved': self.method() found via MRO walk up inheritance chain
    (confidence_score=0.9). High confidence: MRO walk is deterministic.
  - 'constructor_type_trace': self.attr.method() resolved via constructor
    assignment tracking, exactly 1 type assigned to attr
    (confidence_score=0.85). Moderate-high: heuristic but unambiguous.
  - 'name_heuristic_ambiguous': self.attr.method() or interface implementor
    with 2+ possible target types (confidence_score computed dynamically via
    _ambiguous_confidence(num_candidates)). Low confidence: multiple candidates,
    may pick wrong one. Formula: max(0.15, 0.6/num_candidates) ensures confidence
    decays with fan-out but never drops below 0.15 floor. For 2-5 candidates,
    this yields 0.3-0.2 (firmly 'low' per the >=0.7 'high' boundary); for 20+
    candidates, it yields 0.15 floor.
  - 'unknown': fallback for initialization defaults (confidence_score=0.7).

Legacy string confidence values ('high'/'low'/'none') are preserved for
backward compatibility with existing consumers (graph_queries.py's string
equality filter "AND confidence='high'"). Derivation: confidence='high' iff
confidence_score >= 0.7, else confidence='low' ('none' never written by resolver).

The public entry point is :func:`resolve_edges`.

This package is a re-export facade — implementation is split by seam:
  - constants.py:       confidence constants + _ambiguous_confidence formula
  - indexes.py:          build_definition_index / build_constructor_index / build_inheritance_index
  - helpers.py:           internal name/type-resolution helpers
  - call_resolution.py:   resolve_call_site and its per-case implementations
  - edges.py:             resolve_edges public entry point + _find_class_fqn
"""
from __future__ import annotations

from .call_resolution import resolve_call_site
from .constants import CONF_HIGH, CONF_LOW, CONF_NONE, _ambiguous_confidence
from .edges import _find_class_fqn, resolve_edges
from .indexes import build_constructor_index, build_definition_index, build_inheritance_index

__all__ = [
    "CONF_HIGH",
    "CONF_LOW",
    "CONF_NONE",
    "build_definition_index",
    "build_constructor_index",
    "build_inheritance_index",
    "resolve_call_site",
    "resolve_edges",
]
