"""Source-level import statement extraction (Python / TS / JS)."""
from __future__ import annotations

import ast
import re

_RE_TS_IMPORT = re.compile(
    r"""(?m)^\s*import\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]\s*;?"""
)
_RE_TS_REQUIRE = re.compile(r"""(?m)require\(\s*['"]([^'"]+)['"]\s*\)""")


def _extract_python_imports(content: str) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(content)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    return out


def _extract_ts_js_imports(content: str) -> list[str]:
    out = [m.group(1) for m in _RE_TS_IMPORT.finditer(content)]
    out.extend(m.group(1) for m in _RE_TS_REQUIRE.finditer(content))
    return out
