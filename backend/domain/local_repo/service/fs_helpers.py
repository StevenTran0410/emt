"""Filesystem/path helpers: normalization, containment checks, size scans."""
import asyncio
import fnmatch
import os
import shutil
import stat
from pathlib import Path

from ..types import RepoSourceType

# Directories that indicate the repo may be heavy to scan
_SIZE_WARNING_DIRS = frozenset({"node_modules", ".venv", "venv", "env", "target", "build", "dist"})
# Workspace-level ignore defaults (read-only at repository setup screen)
_WORKSPACE_DEFAULT_IGNORES = [".git/**", "node_modules/**", ".venv/**", "venv/**", "dist/**", "build/**", "target/**"]
# If the root contains this many immediate entries, warn
_ROOT_ENTRY_THRESHOLD = 200


def _normalize_repo_path(path: str) -> str:
    """Canonicalize user path so add/remove checks are stable on Windows."""
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(Path(path).absolute())


def _detect_source_type(url: str) -> RepoSourceType:
    """Infer repo host from a git URL (HTTPS or SSH) for display purposes."""
    u = url.lower()
    if "github.com" in u:
        return RepoSourceType.GITHUB
    if "bitbucket.org" in u:
        return RepoSourceType.BITBUCKET
    return RepoSourceType.LOCAL_FOLDER


def _is_under_path(path: Path, root: Path) -> bool:
    """Robust Windows-safe containment check."""
    try:
        p = str(path.resolve())
        r = str(root.resolve())
        return os.path.commonpath([p, r]) == r
    except Exception:
        return False


def _remove_tree_strict(path: Path) -> None:
    """Delete folder/file strictly (handles read-only files on Windows)."""
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
        return

    def _onerror(func, target, _exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_onerror)
    if path.exists():
        raise ValueError(f"Cannot delete managed clone folder: {path}")


def _check_size_warning_sync(path: str) -> tuple[bool, str | None]:
    """Quick check for large-repo indicators (blocking — run in thread)."""
    p = Path(path)
    try:
        entries = list(p.iterdir())
    except PermissionError:
        return False, None

    if len(entries) > _ROOT_ENTRY_THRESHOLD:
        return True, f"Root directory has {len(entries)} entries — scan may be slow"

    heavy = [e.name for e in entries if e.name in _SIZE_WARNING_DIRS]
    if heavy:
        return True, f"Contains heavy directories: {', '.join(heavy[:3])}"

    return False, None


async def _check_size_warning(path: str) -> tuple[bool, str | None]:
    return await asyncio.to_thread(_check_size_warning_sync, path)


def _count_files_with_ignores_sync(
    root: Path,
    ignore_patterns: list[str],
    include_tests: bool = True,
) -> int:
    dir_prefixes = [p[:-3] for p in ignore_patterns if p.endswith("/**")]
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""

        # prune ignored directories early
        kept_dirs: list[str] = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in dir_prefixes):
                continue
            if not include_tests and "test" in d.lower():
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if any(fnmatch.fnmatch(rel, p) for p in ignore_patterns):
                continue
            if not include_tests and any("test" in seg for seg in rel.lower().split("/")):
                continue
            count += 1
    return count
