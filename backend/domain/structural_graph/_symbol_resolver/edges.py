"""Public resolve_edges entry point: calls, extends, and instantiates edges."""
from __future__ import annotations

import logging

from ..symbol_graph import SymbolEdge
from .._symbol_parser import ParsedFile, SymbolInfo
from .call_resolution import resolve_call_site
from .helpers import _resolve_type_name
from .indexes import build_constructor_index, build_definition_index, build_inheritance_index

logger = logging.getLogger(__name__)


def _find_class_fqn(name: str, def_index: dict[str, SymbolInfo]) -> str | None:
    """Find the class/interface symbol FQN for name. Returns FQN if EXACTLY ONE matching class exists, else None (unique-or-drop)."""
    matches = [
        fqn for fqn, sym in def_index.items()
        if sym.method_name == name and sym.kind in ("class", "interface") and sym.class_name is None
    ]
    return matches[0] if len(matches) == 1 else None


def resolve_edges(parsed_files: dict[str, ParsedFile]) -> list[SymbolEdge]:
    """Resolve all call sites, inheritance (extends), and instantiation (instantiates) in all files to SymbolEdge objects.

    Args:
        parsed_files: All parsed files keyed by filename.

    Returns:
        Deduplicated list of :class:`~.symbol_graph.SymbolEdge` objects.
    """
    def_index = build_definition_index(parsed_files)
    ctor_index = build_constructor_index(parsed_files)
    inh_index = build_inheritance_index(parsed_files)

    seen: set[tuple[str, str, str, str]] = set()
    edges: list[SymbolEdge] = []

    def _add_edge(edge: SymbolEdge) -> None:
        key = (edge.src_symbol, edge.dst_symbol, edge.edge_type, edge.confidence)
        if key not in seen:
            seen.add(key)
            edges.append(edge)

    for pf in parsed_files.values():
        for call in pf.call_sites:
            try:
                new_edges = resolve_call_site(
                    call, pf, parsed_files, def_index, ctor_index, inh_index
                )
                for edge in new_edges:
                    key = (edge.src_symbol, edge.dst_symbol, edge.edge_type, edge.confidence)
                    if key not in seen:
                        seen.add(key)
                        edges.append(edge)
            except Exception:
                logger.exception(
                    "resolve_edges: failed call site %s in %s",
                    call.method_name,
                    pf.filename,
                )

        # Resolve extends edges
        for cls, bases in pf.inheritance.items():
            src = f"{pf.filename}::{cls}"
            for base in bases:
                res_name = _resolve_type_name(base, pf, def_index)
                if res_name:
                    base_fqn = _find_class_fqn(res_name, def_index)
                    if base_fqn:
                        _add_edge(
                            SymbolEdge(
                                src_symbol=src,
                                dst_symbol=base_fqn,
                                edge_type="extends",
                                confidence_score=1.0,
                                resolution_method="inheritance",
                            )
                        )

        # Resolve instantiates edges — only real constructions (self.attr = Type()); annotations/param-aliases type the attr for call-resolution but do not instantiate.
        for assign in pf.attribute_assignments:
            if not assign.is_construction:
                continue
            res_type = _resolve_type_name(assign.assigned_type, pf, def_index)
            if res_type:
                class_fqn = _find_class_fqn(res_type, def_index)
                if class_fqn:
                    m_name = assign.method_name or "__init__"
                    src = f"{pf.filename}::{assign.class_name}.{m_name}"
                    _add_edge(
                        SymbolEdge(
                            src_symbol=src,
                            dst_symbol=class_fqn,
                            edge_type="instantiates",
                            confidence_score=1.0,
                            resolution_method="constructor_type_trace",
                        )
                    )

    return edges
