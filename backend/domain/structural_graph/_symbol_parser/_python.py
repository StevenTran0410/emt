"""Python source parser (ast module).

Extracted from the original ``_symbol_parser.py`` module (see package
``__init__.py`` for the parser entry point and overview).
"""
from __future__ import annotations

import ast

from ._models import AttributeAssign, CallSite, ImportInfo, ParsedFile, SymbolInfo


# ---------------------------------------------------------------------------
# Python parser (ast module)
# ---------------------------------------------------------------------------

def _parse_python(filename: str, source: str) -> ParsedFile:
    result = ParsedFile(filename=filename, language="python")
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return result

    # Pass 1 — collect imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name
                result.imports[local] = ImportInfo(
                    local_name=local,
                    src_file=_module_to_file(alias.name),
                    orig_name=alias.name,
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            src_file = _module_to_file(module)
            for alias in node.names:
                if alias.name == "*":
                    result.imports[f"*:{module}"] = ImportInfo(
                        local_name=f"*:{module}",
                        src_file=src_file,
                        orig_name=None,
                        is_star=True,
                    )
                else:
                    local = alias.asname if alias.asname else alias.name
                    result.imports[local] = ImportInfo(
                        local_name=local,
                        src_file=src_file,
                        orig_name=alias.name,
                    )

    # Pass 2 — collect class/function definitions
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [_ast_name(b) for b in node.bases if _ast_name(b)]
            fqn = f"{filename}::{node.name}"
            sym = SymbolInfo(
                fqn=fqn,
                filename=filename,
                class_name=None,
                method_name=node.name,
                kind="class",
                line_start=node.lineno,
                base_classes=bases,
            )
            result.definitions[fqn] = sym
            result.inheritance[node.name] = bases

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mfqn = f"{filename}::{node.name}.{item.name}"
                    result.definitions[mfqn] = SymbolInfo(
                        fqn=mfqn,
                        filename=filename,
                        class_name=node.name,
                        method_name=item.name,
                        kind="method",
                        line_start=item.lineno,
                    )

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Module-level functions only (skip those inside classes)
            parent_is_class = any(
                isinstance(p, ast.ClassDef)
                for p in ast.walk(tree)
                if isinstance(p, ast.ClassDef) and any(
                    isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c is node
                    for c in ast.walk(p)
                    if c is not p
                )
            )
            if not parent_is_class:
                fqn = f"{filename}::{node.name}"
                if fqn not in result.definitions:
                    result.definitions[fqn] = SymbolInfo(
                        fqn=fqn,
                        filename=filename,
                        class_name=None,
                        method_name=node.name,
                        kind="function",
                        line_start=node.lineno,
                    )

    # Pass 3 — collect call sites and attribute assignments using a visitor
    visitor = _PythonVisitor(filename, result.definitions)
    visitor.visit(tree)
    result.call_sites = visitor.call_sites
    result.attribute_assignments = visitor.attribute_assignments

    return result


class _PythonVisitor(ast.NodeVisitor):
    """Extracts call sites and attribute assignments from a Python AST."""

    def __init__(self, filename: str, definitions: dict[str, SymbolInfo]) -> None:
        self.filename = filename
        self.definitions = definitions
        self.call_sites: list[CallSite] = []
        self.attribute_assignments: list[AttributeAssign] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        # Scan for self.attr = Type(), self.attr: Type, or self.attr = annotated_param assignments
        if self._class_stack and len(self._func_stack) == 1:
            current_class = self._class_stack[-1]
            current_method = self._func_stack[-1]

            param_annotations: dict[str, str] = {}
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation:
                    ann_str = _ast_expr_str(arg.annotation)
                    if ann_str:
                        param_annotations[arg.arg] = ann_str

            nested_func_stmts = {
                child_stmt
                for child in ast.walk(node)
                if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                for child_stmt in ast.walk(child)
            }

            for stmt in ast.walk(node):
                if stmt in nested_func_stmts:
                    continue

                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                            assigned_type: str | None = None
                            is_ctor = False
                            if isinstance(stmt.value, ast.Call):
                                assigned_type = _ast_call_name(stmt.value)
                                is_ctor = True
                            elif isinstance(stmt.value, ast.Name) and stmt.value.id in param_annotations:
                                assigned_type = param_annotations[stmt.value.id]

                            if assigned_type:
                                self.attribute_assignments.append(
                                    AttributeAssign(
                                        class_name=current_class,
                                        attr_name=target.attr,
                                        assigned_type=assigned_type,
                                        line=stmt.lineno,
                                        method_name=current_method,
                                        is_construction=is_ctor,
                                    )
                                )

                elif isinstance(stmt, ast.AnnAssign):
                    target = stmt.target
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                        ann_type = _ast_expr_str(stmt.annotation)
                        if ann_type:
                            self.attribute_assignments.append(
                                AttributeAssign(
                                    class_name=current_class,
                                    attr_name=target.attr,
                                    assigned_type=ann_type,
                                    line=stmt.lineno,
                                    method_name=current_method,
                                    is_construction=False,
                                )
                            )
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        caller_fqn = self._current_fqn()
        if caller_fqn is None:
            self.generic_visit(node)
            return

        func = node.func
        if isinstance(func, ast.Attribute):
            receiver_str = _ast_expr_str(func.value)
            is_self = isinstance(func.value, ast.Name) and func.value.id == "self"
            self.call_sites.append(
                CallSite(
                    caller_fqn=caller_fqn,
                    receiver=receiver_str,
                    method_name=func.attr,
                    line=node.lineno,
                    is_self_call=is_self,
                )
            )
        elif isinstance(func, ast.Name):
            self.call_sites.append(
                CallSite(
                    caller_fqn=caller_fqn,
                    receiver=None,
                    method_name=func.id,
                    line=node.lineno,
                )
            )

        self.generic_visit(node)

    def _current_fqn(self) -> str | None:
        if not self._func_stack:
            return None
        if self._class_stack:
            return f"{self.filename}::{self._class_stack[-1]}.{self._func_stack[-1]}"
        return f"{self.filename}::{self._func_stack[-1]}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_to_file(module: str) -> str | None:
    """Best-effort conversion of a Python module name to a file path."""
    if not module:
        return None
    return module.replace(".", "/") + ".py"


def _ast_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _ast_call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _ast_expr_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _ast_expr_str(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    return None
