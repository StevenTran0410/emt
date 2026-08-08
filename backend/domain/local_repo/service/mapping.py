"""DB row → domain model mapping."""
import json

from ..types import LocalRepo, RepoSourceType


def _row_to_model(row) -> LocalRepo:
    try:
        ignore_overrides = json.loads(row["ignore_overrides"] or "[]")
    except Exception:
        ignore_overrides = []
    return LocalRepo(
        id=row["id"],
        workspace_id=row["workspace_id"] if "workspace_id" in row.keys() else None,
        path=row["path"],
        name=row["name"],
        source_type=RepoSourceType(row["source_type"]),
        is_git_repo=bool(row["is_git_repo"]),
        git_branch=row["git_branch"],
        git_head_hash=row["git_head_hash"],
        git_remote_url=row["git_remote_url"],
        has_size_warning=bool(row["has_size_warning"]),
        selected_branch=row["selected_branch"],
        active_snapshot_id=row["active_snapshot_id"],
        sync_mode=row["sync_mode"],
        pinned_ref=row["pinned_ref"],
        ignore_overrides=ignore_overrides,
        detect_submodules=bool(row["detect_submodules"]),
        include_tests=bool(row["include_tests"]) if "include_tests" in row.keys() else False,
        mode=row["mode"] if "mode" in row.keys() else "code_analysis",
        added_at=row["added_at"],
        last_validated_at=row["last_validated_at"],
    )
