"""Data structures shared across the symbol_parser package.

Extracted from the original ``_symbol_parser.py`` module (see package
``__init__.py`` for the parser entry point and overview).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ImportInfo:
    """A single imported name visible in a file's namespace.

    Attributes:
        local_name:  The name as it is referenced in this file.
        src_file:    The file that exports the symbol (resolved best-effort).
                     None if unresolvable (e.g. stdlib or unrecognised path).
        orig_name:   The exported name in the source file.
                     None for ``import module`` style (local_name == module).
        is_star:     True when this entry comes from a ``from X import *``
                     statement.  The actual exported names are unknown.
    """

    local_name: str
    src_file: str | None
    orig_name: str | None
    is_star: bool = False


@dataclass
class SymbolInfo:
    """A definition (function, method, or class) extracted from a file.

    Attributes:
        fqn:          Fully-qualified name in ``file::Class.method`` form.
        filename:     Source file path as given to parse_file().
        class_name:   Enclosing class name, or None for module-level symbols.
        method_name:  Simple name of the function/method/class.
        kind:         ``"function"``, ``"method"``, or ``"class"``.
        line_start:   1-based line number of the definition.
        base_classes: Direct base class names (populated for classes only).
    """

    fqn: str
    filename: str
    class_name: str | None
    method_name: str
    kind: str
    line_start: int
    base_classes: list[str] = field(default_factory=list)


@dataclass
class AttributeAssign:
    """A ``self.attr = SomeClass()`` assignment observed inside a class.

    Attributes:
        class_name:    Class in which the assignment appears.
        attr_name:     Attribute name (``self.<attr_name>``).
        assigned_type: Name of the class being constructed (right-hand side).
        line:          1-based line number of the assignment.
    """

    class_name: str
    attr_name: str
    assigned_type: str
    line: int
    method_name: str | None = None
    # True only for self.attr = Type() (real construction); False for type annotations / param-aliases, which type the attribute for call-resolution but do not instantiate.
    is_construction: bool = True


@dataclass
class CallSite:
    """A call expression observed inside a function or method body.

    Attributes:
        caller_fqn:   FQN of the enclosing function/method.
        receiver:     Attribute-access receiver, e.g. ``self._graph`` for
                      ``self._graph.build()``.  None for bare calls.
        method_name:  The called function/method name.
        line:         1-based line number of the call.
        is_self_call: True when the call is ``self.method()``, meaning
                      receiver resolution should use the caller's class.
    """

    caller_fqn: str
    receiver: str | None
    method_name: str
    line: int
    is_self_call: bool = False


@dataclass
class ParsedFile:
    """All symbol information extracted from a single source file.

    Attributes:
        filename:              Source file path.
        language:              ``"python"`` or ``"typescript"``.
        definitions:           Map from FQN to SymbolInfo.
        imports:               Map from local_name to ImportInfo.
        call_sites:            All call sites found in the file.
        attribute_assignments: All ``self.attr = Type()`` assignments.
        inheritance:           Map from class name to list of base class names.
    """

    filename: str
    language: str
    definitions: dict[str, SymbolInfo] = field(default_factory=dict)
    imports: dict[str, ImportInfo] = field(default_factory=dict)
    call_sites: list[CallSite] = field(default_factory=list)
    attribute_assignments: list[AttributeAssign] = field(default_factory=list)
    inheritance: dict[str, list[str]] = field(default_factory=dict)
