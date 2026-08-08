"""TypeScript/JavaScript source parser (tree-sitter).

Extracted from the original ``_symbol_parser.py`` module (see package
``__init__.py`` for the parser entry point and overview).
"""
from __future__ import annotations

import logging

from ._models import AttributeAssign, CallSite, ImportInfo, ParsedFile, SymbolInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypeScript parser (tree-sitter)
# ---------------------------------------------------------------------------

def _parse_typescript(filename: str, source: str, lang_name: str = "typescript") -> ParsedFile:
    result = ParsedFile(filename=filename, language=lang_name)
    try:
        from ._ts_loaders import _load_ts_language, _ts_parse, _node_text
    except ImportError:
        logger.warning("tree-sitter not available; skipping TS parse for %s", filename)
        return result

    lang = _load_ts_language(lang_name)
    if lang is None:
        return result

    root = _ts_parse(source, lang)
    if root is None:
        return result

    lines = source.splitlines()

    def text_at(node: object) -> str:
        return _node_text(node) or ""

    def node_line(node: object) -> int:
        try:
            return node.start_point[0] + 1  # type: ignore[union-attr]
        except Exception:
            return 0

    # Track interface names so we can apply EC-7 logic later
    interface_names: set[str] = set()

    # Pass 1 — imports
    for node in _ts_walk(root):
        try:
            if node.type == "import_statement":
                _parse_ts_import(node, filename, result, text_at)
        except Exception:
            pass

    # Pass 2 — definitions (class, interface, function)
    _current_class: list[str] = []

    for node in _ts_walk(root):
        try:
            if node.type in ("class_declaration", "abstract_class_declaration"):
                cname_node = node.child_by_field_name("name")
                cname = text_at(cname_node) if cname_node else None
                if cname:
                    bases: list[str] = []
                    # heritage clause: implements / extends
                    for child in node.children:
                        if child.type in ("class_heritage", "implements_clause", "extends_clause"):
                            for t in _ts_walk(child):
                                if t.type == "type_identifier":
                                    bases.append(text_at(t))
                    fqn = f"{filename}::{cname}"
                    result.definitions[fqn] = SymbolInfo(
                        fqn=fqn,
                        filename=filename,
                        class_name=None,
                        method_name=cname,
                        kind="class",
                        line_start=node_line(node),
                        base_classes=bases,
                    )
                    result.inheritance[cname] = bases

            elif node.type == "interface_declaration":
                iname_node = node.child_by_field_name("name")
                iname = text_at(iname_node) if iname_node else None
                if iname:
                    interface_names.add(iname)
                    fqn = f"{filename}::{iname}"
                    result.definitions[fqn] = SymbolInfo(
                        fqn=fqn,
                        filename=filename,
                        class_name=None,
                        method_name=iname,
                        kind="interface",
                        line_start=node_line(node),
                    )

            elif node.type in ("function_declaration", "function"):
                fname_node = node.child_by_field_name("name")
                fname = text_at(fname_node) if fname_node else None
                if fname:
                    fqn = f"{filename}::{fname}"
                    if fqn not in result.definitions:
                        result.definitions[fqn] = SymbolInfo(
                            fqn=fqn,
                            filename=filename,
                            class_name=None,
                            method_name=fname,
                            kind="function",
                            line_start=node_line(node),
                        )
        except Exception:
            pass

    # Pass 3 — methods inside classes
    for node in _ts_walk(root):
        try:
            if node.type in ("class_declaration", "abstract_class_declaration"):
                cname_node = node.child_by_field_name("name")
                cname = text_at(cname_node) if cname_node else None
                if not cname:
                    continue
                body = node.child_by_field_name("body")
                if body is None:
                    continue
                for child in body.children:
                    if child.type in ("method_definition", "public_field_definition"):
                        mname_node = child.child_by_field_name("name")
                        mname = text_at(mname_node) if mname_node else None
                        if mname:
                            mfqn = f"{filename}::{cname}.{mname}"
                            result.definitions[mfqn] = SymbolInfo(
                                fqn=mfqn,
                                filename=filename,
                                class_name=cname,
                                method_name=mname,
                                kind="method",
                                line_start=node_line(child),
                            )
        except Exception:
            pass

    # Pass 4 — call sites and constructor assignments
    _parse_ts_calls(root, filename, result, text_at, node_line, interface_names)

    return result


def _parse_ts_import(
    node: object,
    filename: str,
    result: ParsedFile,
    text_at: object,
) -> None:
    """Extract import bindings from a TypeScript import_statement node."""
    source_node = None
    clause_node = None
    for child in node.children:  # type: ignore[union-attr]
        if child.type == "string":
            source_node = child
        elif child.type == "import_clause":
            clause_node = child

    if source_node is None:
        return

    raw_path = text_at(source_node).strip("'\"")  # type: ignore[operator]
    src_file = _ts_module_to_file(raw_path)

    if clause_node is None:
        return

    for child in _ts_walk(clause_node):
        if child.type == "namespace_import":
            # import * as X — treat as star
            alias_node = child.child_by_field_name("name")
            local = text_at(alias_node) if alias_node else None
            if local:
                result.imports[local] = ImportInfo(
                    local_name=local,
                    src_file=src_file,
                    orig_name=None,
                    is_star=True,
                )
        elif child.type == "named_imports":
            for spec in child.children:
                if spec.type == "import_specifier":
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    orig = text_at(name_node) if name_node else None
                    local = text_at(alias_node) if alias_node else orig
                    if local and orig:
                        result.imports[local] = ImportInfo(
                            local_name=local,
                            src_file=src_file,
                            orig_name=orig,
                        )
        elif child.type == "identifier":
            # default import: import X from '...'
            local = text_at(child)
            if local:
                result.imports[local] = ImportInfo(
                    local_name=local,
                    src_file=src_file,
                    orig_name="default",
                )


def _parse_ts_calls(
    root: object,
    filename: str,
    result: ParsedFile,
    text_at: object,
    node_line: object,
    interface_names: set[str],
) -> None:
    """Walk the TS AST to collect call sites and constructor assignments."""
    # Track class/method context via stacks to compute each call site's caller FQN.
    class_stack: list[str] = []
    method_stack: list[str] = []

    def caller_fqn() -> str | None:
        if not method_stack:
            return None
        if class_stack:
            return f"{filename}::{class_stack[-1]}.{method_stack[-1]}"
        return f"{filename}::{method_stack[-1]}"

    def walk_node(node: object) -> None:
        try:
            ntype = node.type  # type: ignore[union-attr]
        except Exception:
            return

        pushed_class = False
        pushed_method = False

        try:
            if ntype in ("class_declaration", "abstract_class_declaration"):
                cname_node = node.child_by_field_name("name")  # type: ignore[union-attr]
                cname = text_at(cname_node) if cname_node else None
                if cname:
                    class_stack.append(cname)
                    pushed_class = True

            elif ntype in ("method_definition", "function_declaration", "function", "arrow_function"):
                mname_node = node.child_by_field_name("name")  # type: ignore[union-attr]
                mname = text_at(mname_node) if mname_node else None
                if mname:
                    method_stack.append(mname)
                    pushed_method = True

            elif ntype == "call_expression":
                fqn = caller_fqn()
                if fqn:
                    func_node = node.child_by_field_name("function")  # type: ignore[union-attr]
                    if func_node and func_node.type == "member_expression":
                        obj_node = func_node.child_by_field_name("object")
                        prop_node = func_node.child_by_field_name("property")
                        receiver = text_at(obj_node) if obj_node else None
                        method_name = text_at(prop_node) if prop_node else None
                        if method_name:
                            is_self = receiver in ("this", "self")
                            result.call_sites.append(
                                CallSite(
                                    caller_fqn=fqn,
                                    receiver=receiver,
                                    method_name=method_name,
                                    line=node_line(node),  # type: ignore[operator]
                                    is_self_call=is_self,
                                )
                            )
                    elif func_node and func_node.type == "identifier":
                        mname_called = text_at(func_node)
                        if mname_called:
                            result.call_sites.append(
                                CallSite(
                                    caller_fqn=fqn,
                                    receiver=None,
                                    method_name=mname_called,
                                    line=node_line(node),  # type: ignore[operator]
                                )
                            )

            elif ntype == "new_expression":
                # this.x = new ClassName() in constructor
                constructor_node = node.child_by_field_name("constructor")  # type: ignore[union-attr]
                ctor_type = text_at(constructor_node) if constructor_node else None
                if ctor_type and class_stack and method_stack and method_stack[-1] == "constructor":
                    # Look for parent assignment: this.x = new ...
                    pass  # handled via expression_statement walk below

        except Exception:
            pass

        try:
            for child in node.children:  # type: ignore[union-attr]
                walk_node(child)
        except Exception:
            pass

        if pushed_method:
            if method_stack:
                method_stack.pop()
        if pushed_class:
            if class_stack:
                class_stack.pop()

    # Also scan for this.x = new ClassName() in constructor methods
    def scan_ctor_assignments(node: object, class_name: str) -> None:
        try:
            if node.type in ("expression_statement",):  # type: ignore[union-attr]
                for child in node.children:  # type: ignore[union-attr]
                    if child.type == "assignment_expression":
                        left = child.child_by_field_name("left")
                        right = child.child_by_field_name("right")
                        if (
                            left is not None
                            and right is not None
                            and left.type == "member_expression"
                        ):
                            obj = left.child_by_field_name("object")
                            prop = left.child_by_field_name("property")
                            if obj and text_at(obj) in ("this", "self") and prop:
                                attr = text_at(prop)
                                if right.type == "new_expression":
                                    ctor_node = right.child_by_field_name("constructor")
                                    ctor_type = text_at(ctor_node) if ctor_node else None
                                    if attr and ctor_type:
                                        result.attribute_assignments.append(
                                            AttributeAssign(
                                                class_name=class_name,
                                                attr_name=attr,
                                                assigned_type=ctor_type,
                                                line=node_line(node),  # type: ignore[operator]
                                            )
                                        )
            for child in node.children:  # type: ignore[union-attr]
                scan_ctor_assignments(child, class_name)
        except Exception:
            pass

    # Run constructor assignment scan per class
    for node in _ts_walk(root):
        try:
            if node.type in ("class_declaration", "abstract_class_declaration"):
                cname_node = node.child_by_field_name("name")
                cname = text_at(cname_node) if cname_node else None
                if cname:
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            if child.type == "method_definition":
                                mname_node = child.child_by_field_name("name")
                                mname = text_at(mname_node) if mname_node else None
                                if mname == "constructor":
                                    scan_ctor_assignments(child, cname)
        except Exception:
            pass

    walk_node(root)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_walk(node: object):
    """Breadth-first walk of a tree-sitter node."""
    queue = [node]
    while queue:
        current = queue.pop(0)
        yield current
        try:
            queue.extend(current.children)  # type: ignore[union-attr]
        except Exception:
            pass


def _ts_module_to_file(path: str) -> str | None:
    """Best-effort conversion of a TS import path to a file path."""
    if not path:
        return None
    if path.startswith("."):
        for ext in (".ts", ".tsx", ".js"):
            if path.endswith(ext):
                return path
        return path + ".ts"
    return None
