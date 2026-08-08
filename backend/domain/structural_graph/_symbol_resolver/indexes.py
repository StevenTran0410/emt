"""Index builders used by call-site and edge resolution.

Builds flat lookups from the per-file parse results: FQN-to-definition,
(file, class, attr)-to-assigned-types, and class-to-base-classes.
"""
from __future__ import annotations

from .._symbol_parser import ParsedFile, SymbolInfo


def build_definition_index(
    parsed_files: dict[str, ParsedFile],
) -> dict[str, SymbolInfo]:
    """Return a flat O(1) lookup from FQN to SymbolInfo across all files.

    Args:
        parsed_files: All parsed files keyed by filename.

    Returns:
        Dict mapping FQN strings to :class:`~._symbol_parser.SymbolInfo`.
    """
    index: dict[str, SymbolInfo] = {}
    for pf in parsed_files.values():
        index.update(pf.definitions)
    return index


def build_constructor_index(
    parsed_files: dict[str, ParsedFile],
) -> dict[tuple[str, str, str], list[str]]:
    """Return an index from (filename, class_name, attr_name) to assigned types.

    Multiple entries in the list indicate the attribute is reassigned in more
    than one method — making it ambiguous (EC-3).

    Args:
        parsed_files: All parsed files keyed by filename.

    Returns:
        Dict mapping ``(filename, class_name, attr_name)`` to a list of
        assigned constructor type names.
    """
    index: dict[tuple[str, str, str], list[str]] = {}
    for pf in parsed_files.values():
        for assign in pf.attribute_assignments:
            key = (pf.filename, assign.class_name, assign.attr_name)
            index.setdefault(key, [])
            if assign.assigned_type not in index[key]:
                index[key].append(assign.assigned_type)
    return index


def build_inheritance_index(
    parsed_files: dict[str, ParsedFile],
) -> dict[str, list[str]]:
    """Return a flat map from class name to list of direct base class names.

    Collects inheritance across ALL parsed files.

    Args:
        parsed_files: All parsed files keyed by filename.

    Returns:
        Dict mapping class name to base class name list.
    """
    index: dict[str, list[str]] = {}
    for pf in parsed_files.values():
        for cls, bases in pf.inheritance.items():
            index.setdefault(cls, [])
            for b in bases:
                if b not in index[cls]:
                    index[cls].append(b)
    return index
