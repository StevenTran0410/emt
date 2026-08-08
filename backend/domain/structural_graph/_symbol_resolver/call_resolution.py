"""Call site resolution: bare calls, self.method(), self.attr.method(), module.func()."""
from __future__ import annotations

import logging

from ..symbol_graph import SymbolEdge
from .._symbol_parser import CallSite, ImportInfo, ParsedFile, SymbolInfo
from .constants import _ambiguous_confidence
from .helpers import (
    _caller_class,
    _extract_self_attr,
    _find_implementors,
    _find_method_fqn,
    _lookup_import_def,
    _resolve_type_name,
)

logger = logging.getLogger(__name__)


def resolve_call_site(
    call: CallSite,
    caller_file: ParsedFile,
    all_files: dict[str, ParsedFile],
    def_index: dict[str, SymbolInfo],
    ctor_index: dict[tuple[str, str, str], list[str]],
    inh_index: dict[str, list[str]],
) -> list[SymbolEdge]:
    """Resolve a single call site to zero or more SymbolEdge objects.

    Confidence decision tree:

    1. ``receiver`` is None → bare function call.
       - Look up ``call.method_name`` in the caller file's import namespace.
       - If found with a known src_file, look for exactly one definition
         ``src_file::method_name`` → CONF_HIGH.
       - If from a star import → return [] (EC-9).
       - If not found in imports, search def_index for same-filename
         definitions only.

    2. ``is_self_call`` is True → ``self.method()`` call.
       - Determine caller's class from caller_fqn.
       - Look for method definition in that class first.
       - Walk MRO via inh_index until found or exhausted.
       - CONF_HIGH if single base found in def_index, CONF_LOW otherwise.

    3. ``receiver`` is ``self.attr`` style.
       - Look up ``(caller_file.filename, class_name, attr)`` in ctor_index.
       - 0 types → CONF_NONE (EC-6 duck typing also falls here).
       - 1 type → resolve to that type's method → CONF_HIGH (EC-2).
       - 2+ types → emit one edge per type with CONF_LOW (EC-3).

    4. Any case where receiver type resolves to an interface name →
       collect all implementing classes → CONF_LOW (EC-7).

    Returns:
        A list of :class:`~.symbol_graph.SymbolEdge` objects.
        Unresolvable call sites return an empty list (never CONF_NONE edges).

    Args:
        call:        The call site to resolve.
        caller_file: The ParsedFile containing this call site.
        all_files:   All ParsedFile objects keyed by filename.
        def_index:   Output of :func:`build_definition_index`.
        ctor_index:  Output of :func:`build_constructor_index`.
        inh_index:   Output of :func:`build_inheritance_index`.
    """
    try:
        return _resolve(call, caller_file, all_files, def_index, ctor_index, inh_index)
    except Exception:
        logger.exception(
            "resolve_call_site failed for %s in %s",
            call.method_name,
            caller_file.filename,
        )
        return []


def _resolve(
    call: CallSite,
    caller_file: ParsedFile,
    all_files: dict[str, ParsedFile],
    def_index: dict[str, SymbolInfo],
    ctor_index: dict[tuple[str, str, str], list[str]],
    inh_index: dict[str, list[str]],
) -> list[SymbolEdge]:
    # Collect interface names from all files for EC-7 detection
    interface_names: set[str] = set()
    for pf in all_files.values():
        for sym in pf.definitions.values():
            if sym.kind == "interface":
                interface_names.add(sym.method_name)

    # ------------------------------------------------------------------ #
    # Case 1 — bare function call (no receiver)
    # ------------------------------------------------------------------ #
    if call.receiver is None:
        return _resolve_bare_call(call, caller_file, def_index)

    # ------------------------------------------------------------------ #
    # Case 2 — self.method() call (direct method, inheritance)
    # ------------------------------------------------------------------ #
    if call.is_self_call and call.receiver in ("self", "this"):
        return _resolve_self_call(call, caller_file, def_index, inh_index)

    # ------------------------------------------------------------------ #
    # Case 3 — self.attr.method() call (attribute receiver)
    # ------------------------------------------------------------------ #
    # Extract the attribute name from a "self.attr" receiver
    attr_name = _extract_self_attr(call.receiver)
    if attr_name:
        class_name = _caller_class(call.caller_fqn)
        if class_name is None:
            return []
        key = (caller_file.filename, class_name, attr_name)
        assigned_types = ctor_index.get(key, [])
        if not assigned_types:
            return []  # EC-6: no type trace

        edges: list[SymbolEdge] = []
        # Determine resolution_method based on number of assigned types
        is_single_type = len(assigned_types) == 1

        for type_name in assigned_types:
            # Resolve the type name via imports if needed
            resolved_type = _resolve_type_name(type_name, caller_file, def_index)
            if resolved_type is None:
                continue

            # EC-7: if resolved_type is an interface, find implementors
            if resolved_type in interface_names:
                implementors = _find_implementors(resolved_type, all_files, def_index)
                for impl_class in implementors:
                    target_fqn = _find_method_fqn(impl_class, call.method_name, def_index, inh_index)
                    if target_fqn:
                        edges.append(
                            SymbolEdge(
                                src_symbol=call.caller_fqn,
                                dst_symbol=target_fqn,
                                edge_type="calls",
                                confidence_score=_ambiguous_confidence(len(implementors)),
                                resolution_method="name_heuristic_ambiguous",
                                evidence_lines=[call.line],
                            )
                        )
                continue

            target_fqn = _find_method_fqn(resolved_type, call.method_name, def_index, inh_index)
            if target_fqn:
                edges.append(
                    SymbolEdge(
                        src_symbol=call.caller_fqn,
                        dst_symbol=target_fqn,
                        edge_type="calls",
                        confidence_score=0.85 if is_single_type else _ambiguous_confidence(len(assigned_types)),
                        resolution_method="constructor_type_trace" if is_single_type else "name_heuristic_ambiguous",
                        evidence_lines=[call.line],
                    )
                )
        return edges

    # ------------------------------------------------------------------ #
    # Case 4 — other attribute receiver (e.g. module.func())
    # ------------------------------------------------------------------ #
    return _resolve_module_call(call, caller_file, def_index)


def _resolve_bare_call(
    call: CallSite,
    caller_file: ParsedFile,
    def_index: dict[str, SymbolInfo],
) -> list[SymbolEdge]:
    method = call.method_name

    # Check for star import that covers this name
    for imp in caller_file.imports.values():
        if imp.is_star:
            # Under a star import we can't know — return empty (EC-9)
            return []

    # Look in import namespace
    imp_info = caller_file.imports.get(method)
    if imp_info:
        if imp_info.is_star:
            return []
        target_fqn = _lookup_import_def(imp_info, method, def_index)
        if target_fqn:
            return [
                SymbolEdge(
                    src_symbol=call.caller_fqn,
                    dst_symbol=target_fqn,
                    edge_type="calls",
                    confidence_score=0.95,
                    resolution_method="import_path_match",
                    evidence_lines=[call.line],
                )
            ]
        return []

    # Not imported — look in same file only
    candidates = [
        fqn
        for fqn, sym in def_index.items()
        if sym.filename == caller_file.filename and sym.method_name == method
    ]
    if len(candidates) == 1:
        return [
            SymbolEdge(
                src_symbol=call.caller_fqn,
                dst_symbol=candidates[0],
                edge_type="calls",
                confidence_score=0.85,
                resolution_method="same_file_scope",
                evidence_lines=[call.line],
            )
        ]
    return []


def _resolve_self_call(
    call: CallSite,
    caller_file: ParsedFile,
    def_index: dict[str, SymbolInfo],
    inh_index: dict[str, list[str]],
) -> list[SymbolEdge]:
    class_name = _caller_class(call.caller_fqn)
    if class_name is None:
        return []

    target_fqn = _find_method_fqn(class_name, call.method_name, def_index, inh_index)
    if target_fqn:
        return [
            SymbolEdge(
                src_symbol=call.caller_fqn,
                dst_symbol=target_fqn,
                edge_type="calls",
                confidence_score=0.9,
                resolution_method="mro_resolved",
                evidence_lines=[call.line],
            )
        ]
    return []


def _resolve_module_call(
    call: CallSite,
    caller_file: ParsedFile,
    def_index: dict[str, SymbolInfo],
) -> list[SymbolEdge]:
    """Handle module.func() style calls via import namespace."""
    if call.receiver is None:
        return []
    # receiver might be an imported module name
    imp_info = caller_file.imports.get(call.receiver)
    if imp_info and not imp_info.is_star and imp_info.src_file:
        target_fqn = _lookup_import_def(
            ImportInfo(
                local_name=call.method_name,
                src_file=imp_info.src_file,
                orig_name=call.method_name,
            ),
            call.method_name,
            def_index,
        )
        if target_fqn:
            return [
                SymbolEdge(
                    src_symbol=call.caller_fqn,
                    dst_symbol=target_fqn,
                    edge_type="calls",
                    confidence_score=0.95,
                    resolution_method="import_path_match",
                    evidence_lines=[call.line],
                )
            ]
    return []
