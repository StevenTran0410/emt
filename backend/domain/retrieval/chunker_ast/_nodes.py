"""AST node collection: symbol-name extraction, oversized-node splitting, and
the top-down span-collection DFS.
"""
from __future__ import annotations

from typing import Any

from ._lang_configs import LanguageConfig
from ._types import _NodeSpan


def _extract_name(node: Any, src_bytes: bytes) -> str:
    """Extract the best-effort symbol name from an AST node.

    Node-type-specific rules (language-aware, applied before the generic fallback):

    Go method_declaration:
        Structure: func <receiver_list> <field_identifier> <params> <result> <body>
        The method name is the field_identifier child, not an identifier.

    C function_definition:
        Simple:  int foo(...)       → function_declarator > identifier
        Pointer: Entry *foo(...)    → pointer_declarator > function_declarator > identifier
        Without special handling the first type_identifier child ("Entry") would be
        returned as the name, which is actually the return type.

    Rust impl_item:
        "impl Trait for Type" has two type_identifier children: trait then type.
        We want the last one (the type being implemented), not the trait name.
        "impl Type" has one type_identifier — still the last one, so the same rule applies.
    """
    node_type = node.type

    # Go: method name lives in a field_identifier child, not identifier.
    if node_type == "method_declaration":
        for child in node.children:
            if child.type == "field_identifier":
                try:
                    return src_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                except Exception:
                    return ""
        return ""

    # C: traverse into function_declarator (or pointer_declarator > function_declarator)
    # to find the actual function name rather than the return type.
    if node_type == "function_definition":
        for child in node.children:
            if child.type == "function_declarator":
                for gc in child.children:
                    if gc.type == "identifier":
                        try:
                            return src_bytes[gc.start_byte:gc.end_byte].decode("utf-8", errors="replace")
                        except Exception:
                            return ""
            elif child.type == "pointer_declarator":
                for gc in child.children:
                    if gc.type == "function_declarator":
                        for ggc in gc.children:
                            if ggc.type == "identifier":
                                try:
                                    return src_bytes[ggc.start_byte:ggc.end_byte].decode("utf-8", errors="replace")
                                except Exception:
                                    return ""
        # Fall through to generic (handles C++ / other languages using function_definition).

    # Rust: "impl Trait for Type" — take the last type_identifier (the concrete type).
    if node_type == "impl_item":
        type_ids = [c for c in node.children if c.type == "type_identifier"]
        if type_ids:
            try:
                last = type_ids[-1]
                return src_bytes[last.start_byte:last.end_byte].decode("utf-8", errors="replace")
            except Exception:
                return ""
        return ""

    # Generic fallback: first identifier-like child.
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier", "type_identifier"):
            try:
                return src_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            except Exception:
                return ""
    return ""


def _split_oversized_span(node: Any, src_bytes: bytes, max_size: int, overlap_ratio: float = 0.05) -> list[_NodeSpan]:
    """Split a node's own byte range into overlapping parts instead of recursing into children -- most large methods have no nested function/class children, so recursion silently dropped their content."""
    start, end = node.start_byte, node.end_byte
    step = max(1, int(max_size * (1 - overlap_ratio)))
    overlap = max_size - step

    bounds: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        part_end = min(pos + max_size, end)
        bounds.append((pos, part_end))
        if part_end >= end:
            break
        pos = part_end - overlap

    name = _extract_name(node, src_bytes)
    total = len(bounds)
    spans: list[_NodeSpan] = []
    for i, (s, e) in enumerate(bounds):
        start_line = node.start_point[0] + src_bytes[node.start_byte:s].count(b"\n")
        end_line = node.start_point[0] + src_bytes[node.start_byte:e].count(b"\n")
        spans.append(_NodeSpan(
            start_byte=s,
            end_byte=e,
            start_line=start_line,
            end_line=end_line,
            node_type=node.type,
            name=name,
            is_import=False,
            split_part=i,
            split_of=total,
        ))
    return spans


def _collect_nodes(
    root: Any,
    src_bytes: bytes,
    cfg: LanguageConfig,
    max_size: int,
) -> list[_NodeSpan]:
    """
    Top-down traversal over the AST using an explicit stack (no Python recursion).

    - Semantic nodes (functions, classes) that fit within max_size are emitted
      as a single span.
    - Semantic nodes larger than max_size are recursively split at their children.
    - Import nodes are collected together into a pending buffer, coalesced, and
      flushed as a single span when a non-import node is encountered.
    - All other nodes fall through to their children.

    Uses an iterative stack to avoid Python recursion limits and reduce
    per-call overhead compared to nested closures.
    """
    semantic_types = cfg.semantic_node_types
    import_types = cfg.import_node_types

    result: list[_NodeSpan] = []
    import_buffer: list[_NodeSpan] = []

    def flush_imports() -> None:
        if import_buffer:
            result.extend(import_buffer)
            import_buffer.clear()

    # Iterative DFS using an explicit stack.
    # Use named_children (skips punctuation/whitespace tokens) for ~5x speedup
    # on the root-level children access vs .children.
    # The stack starts with root's named children in order.
    stack: list[Any] = list(reversed(root.named_children))

    while stack:
        node = stack.pop()
        node_type = node.type

        if node_type in import_types:
            import_buffer.append(_NodeSpan(
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0],
                end_line=node.end_point[0],
                node_type="import_group",
                name="",
                is_import=True,
            ))
            continue

        if node_type in semantic_types:
            flush_imports()
            size = node.end_byte - node.start_byte
            if size <= max_size:
                result.append(_NodeSpan(
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    start_line=node.start_point[0],
                    end_line=node.end_point[0],
                    node_type=node_type,
                    name=_extract_name(node, src_bytes),
                    is_import=False,
                ))
                continue
            # Node too large for one chunk — split its own range instead of recursing into children.
            result.extend(_split_oversized_span(node, src_bytes, max_size))
            continue

        # Non-semantic node — flush imports and descend into named children.
        flush_imports()
        stack.extend(reversed(node.named_children))

    flush_imports()

    # Coalesce adjacent import_group spans into a single merged span so they
    # travel through the merge pass as one unit and don't bleed into function groups.
    coalesced: list[_NodeSpan] = []
    pending_imports: list[_NodeSpan] = []

    def flush_import_spans() -> None:
        if not pending_imports:
            return
        merged = _NodeSpan(
            start_byte=pending_imports[0].start_byte,
            end_byte=pending_imports[-1].end_byte,
            start_line=pending_imports[0].start_line,
            end_line=pending_imports[-1].end_line,
            node_type="import_group",
            name="",
            is_import=True,
        )
        coalesced.append(merged)
        pending_imports.clear()

    for span in result:
        if span.is_import:
            pending_imports.append(span)
        else:
            flush_import_spans()
            coalesced.append(span)
    flush_import_spans()

    return coalesced
