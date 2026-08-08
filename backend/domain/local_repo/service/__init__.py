"""LocalRepoService — path validation, git metadata reading, and CRUD."""
from .clone import CloneMixin
from .crud import CrudMixin
from .fs_helpers import (
    _ROOT_ENTRY_THRESHOLD,
    _SIZE_WARNING_DIRS,
    _WORKSPACE_DEFAULT_IGNORES,
    _check_size_warning,
    _check_size_warning_sync,
    _count_files_with_ignores_sync,
    _detect_source_type,
    _is_under_path,
    _normalize_repo_path,
    _remove_tree_strict,
)
from .git_metadata import GitMetadataMixin
from .mapping import _row_to_model

__all__ = ["LocalRepoService"]


class LocalRepoService(GitMetadataMixin, CrudMixin, CloneMixin):
    pass
