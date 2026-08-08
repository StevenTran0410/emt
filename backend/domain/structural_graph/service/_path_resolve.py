"""Path normalization and import-target resolution helpers."""
from __future__ import annotations

import os
from pathlib import Path


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _is_init_file(path: str) -> bool:
    """Return True for Python __init__.py files.

    These are namespace-package markers that carry no structural information —
    they're either empty or just re-export symbols from sub-modules.  Including
    them as graph nodes adds noise: they generate singleton communities because
    they have near-zero import edges.
    """
    return path == "__init__.py" or path.endswith("/__init__.py")


def _build_py_suffix_index(file_paths: set[str] | list[str]) -> dict[str, str]:
    """Build a suffix-lookup table for Python files.

    Enables resolving absolute imports like "domain.x" → "backend/domain/x.py"
    regardless of the source-root prefix used in the project layout.
    First-seen suffix wins (insertion order = iteration order of the input).
    """
    index: dict[str, str] = {}
    for f in file_paths:
        if f.endswith(".py"):
            parts = f.split("/")
            for i in range(len(parts)):
                suffix = "/".join(parts[i:])
                if suffix not in index:
                    index[suffix] = f
    return index


def _is_entrypoint(rel_path: str) -> bool:
    low = rel_path.lower()
    name = Path(low).name
    if name in {"main.py", "__main__.py", "app.py", "server.py", "cli.py", "index.ts", "index.tsx"}:
        return True
    if "/bin/" in low or "/cmd/" in low:
        return True
    if low.endswith("/manage.py"):
        return True
    if "/routes/" in low and ("index." in name or "router" in name):
        return True
    return False


def _resolve_relative_import(src_rel_path: str, target: str, candidates: set[str]) -> str | None:
    src = Path(src_rel_path)
    base = src.parent
    # os.path.normpath collapses '..' segments that Path.as_posix() leaves raw.
    # Without this, "screens/analysis/../../store/foo" never matches file_set.
    candidate = _normalize(os.path.normpath(str(base / target)))

    options = [
        candidate,
        f"{candidate}.py",
        f"{candidate}.ts",
        f"{candidate}.tsx",
        f"{candidate}.js",
        f"{candidate}.jsx",
        _normalize(str(Path(candidate) / "index.py")),
        _normalize(str(Path(candidate) / "index.ts")),
        _normalize(str(Path(candidate) / "index.tsx")),
        _normalize(str(Path(candidate) / "index.js")),
    ]
    for opt in options:
        if opt in candidates:
            return opt
    return None
