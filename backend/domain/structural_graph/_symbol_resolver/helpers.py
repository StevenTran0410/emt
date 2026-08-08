"""Internal name/type-resolution helpers shared by call-site and edge resolution."""
from __future__ import annotations

from .._symbol_parser import ImportInfo, ParsedFile, SymbolInfo
from .constants import _MAX_MRO_DEPTH


def _caller_class(fqn: str) -> str | None:
    """Extract class name from ``file::ClassName.method`` FQN."""
    if "::" not in fqn:
        return None
    _, rest = fqn.split("::", 1)
    if "." in rest:
        return rest.split(".")[0]
    return None


def _extract_self_attr(receiver: str | None) -> str | None:
    """Return ``attr`` from ``self.attr`` receiver string, else None."""
    if receiver is None:
        return None
    if receiver.startswith("self."):
        return receiver[5:]
    if receiver.startswith("this."):
        return receiver[5:]
    return None


def _find_method_fqn(
    class_name: str,
    method_name: str,
    def_index: dict[str, SymbolInfo],
    inh_index: dict[str, list[str]],
    depth: int = 0,
) -> str | None:
    """Walk the MRO of class_name looking for method_name.

    Returns the first FQN found, or None if not found within _MAX_MRO_DEPTH.
    """
    if depth > _MAX_MRO_DEPTH:
        return None

    # Search def_index for any file that defines class_name.method_name
    for fqn, sym in def_index.items():
        if sym.class_name == class_name and sym.method_name == method_name:
            return fqn

    # Walk base classes
    for base in inh_index.get(class_name, []):
        result = _find_method_fqn(base, method_name, def_index, inh_index, depth + 1)
        if result:
            return result

    return None


def _lookup_import_def(
    imp_info: ImportInfo,
    method_name: str,
    def_index: dict[str, SymbolInfo],
) -> str | None:
    """Return the FQN for a symbol given its ImportInfo."""
    if imp_info.src_file is None:
        return None
    # Try exact file match across all known file variants
    for fqn, sym in def_index.items():
        if (
            sym.method_name == (imp_info.orig_name or method_name)
            and _file_matches(sym.filename, imp_info.src_file)
            and sym.class_name is None  # module-level only for bare imports
        ):
            return fqn
    return None


def _file_matches(actual: str, expected: str) -> bool:
    """Check whether two file paths refer to the same file (suffix match)."""
    a = actual.replace("\\", "/")
    e = expected.replace("\\", "/")
    return a == e or a.endswith("/" + e) or e.endswith("/" + a)


def _resolve_type_name(
    type_name: str,
    caller_file: ParsedFile,
    def_index: dict[str, SymbolInfo],
) -> str | None:
    """Resolve a bare type name to the definitive class name in def_index.

    Checks the caller file's import namespace first, then falls back to the
    type_name as-is if a class with that name exists anywhere in def_index.
    """
    # Check imports
    imp_info = caller_file.imports.get(type_name)
    if imp_info and not imp_info.is_star:
        return imp_info.orig_name or type_name

    # Look for class defined in same file
    for sym in def_index.values():
        if sym.method_name == type_name and sym.kind in ("class", "interface"):
            return type_name

    return type_name


def _find_implementors(
    interface_name: str,
    all_files: dict[str, ParsedFile],
    def_index: dict[str, SymbolInfo],
) -> list[str]:
    """Return class names that implement or extend the given interface."""
    implementors: list[str] = []
    for pf in all_files.values():
        for cls_name, bases in pf.inheritance.items():
            if interface_name in bases:
                implementors.append(cls_name)
    return implementors
