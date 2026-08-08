"""CRUD operations: add/list/get/remove, settings, snapshot, file-count estimate."""
import asyncio
import json
from pathlib import Path

from infrastructure.db.database import get_db
from shared.errors import ConflictError, NotFoundError
from shared.logger import logger
from shared.utils import new_id, utc_now_iso

from domain.snapshot_cleanup import delete_repo_artifacts

from ..types import (
    AddLocalRepoRequest,
    EstimateFileCountResponse,
    LocalRepo,
    RepoSourceType,
    UpdateRepoSettingsRequest,
    ValidateFolderRequest,
)
from .fs_helpers import (
    _WORKSPACE_DEFAULT_IGNORES,
    _count_files_with_ignores_sync,
    _is_under_path,
    _normalize_repo_path,
    _remove_tree_strict,
)
from .mapping import _row_to_model


class CrudMixin:
    async def add(
        self,
        req: AddLocalRepoRequest,
        source_type: RepoSourceType = RepoSourceType.LOCAL_FOLDER,
    ) -> LocalRepo:
        normalized_path = _normalize_repo_path(req.path)
        validation = await self.validate(ValidateFolderRequest(path=normalized_path))
        if not validation.exists or not validation.is_directory:
            raise ValueError(f"Path '{normalized_path}' is not a valid directory")

        db = get_db()
        async with db.execute(
            "SELECT 1 FROM local_repos WHERE path = ? AND workspace_id IS ? AND mode = ?",
            (normalized_path, req.workspace_id, req.mode),
        ) as cur:
            if await cur.fetchone():
                raise ConflictError(f"Folder '{normalized_path}' is already added to this workspace in this mode")

        repo_id = new_id()
        now = utc_now_iso()

        await db.execute(
            """INSERT INTO local_repos
               (id, workspace_id, path, name, source_type, is_git_repo, git_branch,
                git_head_hash, git_remote_url, has_size_warning, selected_branch,
                sync_mode, pinned_ref, ignore_overrides, detect_submodules,
                include_tests, mode, added_at, last_validated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo_id,
                req.workspace_id,
                normalized_path,
                validation.name,
                source_type.value,
                int(validation.is_git_repo),
                validation.git_branch,
                validation.git_head_hash,
                validation.git_remote_url,
                int(validation.has_size_warning),
                None,   # selected_branch defaults to None (use HEAD)
                "latest",
                None,
                "[]",
                1,
                1 if req.mode == "aeh" else 0,
                req.mode,
                now,
                now,
            ),
        )
        await db.commit()
        logger.info(f"Added local repo '{validation.name}' at {req.path}")

        row = await self._fetch_row(repo_id)
        return _row_to_model(row)

    async def list_all(
        self,
        workspace_id: str | None = None,
        mode: str | None = None,
    ) -> list[LocalRepo]:
        db = get_db()
        sql = "SELECT * FROM local_repos"
        params = []
        conditions = []

        if workspace_id is not None:
            conditions.append("workspace_id = ?")
            params.append(workspace_id)
        if mode is not None:
            conditions.append("mode = ?")
            params.append(mode)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY added_at ASC"

        async with db.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]

    async def get_by_id(self, repo_id: str) -> LocalRepo:
        row = await self._fetch_row(repo_id)
        return _row_to_model(row)

    async def remove(self, repo_id: str) -> None:
        db = get_db()
        repo = await self.get_by_id(repo_id)
        await delete_repo_artifacts(repo_id)

        # A path can be shared by multiple rows (e.g. Code Analysis + AEH); only delete the folder once no other row references it.
        async with db.execute(
            "SELECT 1 FROM local_repos WHERE path = ? AND id != ?",
            (repo.path, repo_id),
        ) as cur:
            shared_by_other_repo = await cur.fetchone() is not None

        # If this path is inside CodeSpectra's managed clone root, delete it strictly first to avoid silent leftovers causing clone conflicts.
        managed_root = Path.home() / "CodeSpectra" / "repos"
        repo_path = Path(repo.path)
        if shared_by_other_repo:
            logger.info(f"Skipping folder delete for {repo_path} — still referenced by another repo entry")
        elif _is_under_path(repo_path, managed_root):
            _remove_tree_strict(repo_path)
            logger.info(f"Deleted managed clone folder: {repo_path}")

        async with db.execute("DELETE FROM local_repos WHERE id = ?", (repo_id,)) as cur:
            if cur.rowcount == 0:
                raise NotFoundError("LocalRepo", repo_id)
        await db.commit()

        logger.info(f"Removed local repo {repo_id}")

    async def set_active_snapshot(self, repo_id: str, snapshot_id: str | None) -> LocalRepo:
        repo = await self.get_by_id(repo_id)
        db = get_db()
        if snapshot_id:
            async with db.execute(
                "SELECT 1 FROM repo_snapshots WHERE id=? AND local_repo_id=?",
                (snapshot_id, repo_id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError("Snapshot does not belong to this repository")
        await db.execute(
            "UPDATE local_repos SET active_snapshot_id=? WHERE id=?",
            (snapshot_id, repo.id),
        )
        await db.commit()
        logger.info(f"Set active_snapshot_id={snapshot_id} for repo {repo_id}")
        return await self.get_by_id(repo_id)

    async def update_settings(self, repo_id: str, req: UpdateRepoSettingsRequest) -> LocalRepo:
        await self.get_by_id(repo_id)  # ensure exists
        db = get_db()
        await db.execute(
            """UPDATE local_repos
               SET sync_mode=?, pinned_ref=?, ignore_overrides=?, detect_submodules=?, include_tests=?
               WHERE id=?""",
            (
                req.sync_mode.value,
                req.pinned_ref,
                json.dumps(req.ignore_overrides),
                int(req.detect_submodules),
                int(req.include_tests),
                repo_id,
            ),
        )
        await db.commit()
        logger.info(f"Updated repository settings for {repo_id}")
        return await self.get_by_id(repo_id)

    async def estimate_file_count(self, repo_id: str) -> EstimateFileCountResponse:
        repo = await self.get_by_id(repo_id)
        root = Path(repo.path)
        if not root.exists() or not root.is_dir():
            raise ValueError("Repository path does not exist")

        effective = [*_WORKSPACE_DEFAULT_IGNORES, *repo.ignore_overrides]
        count = await asyncio.to_thread(
            _count_files_with_ignores_sync, root, effective, repo.include_tests
        )
        return EstimateFileCountResponse(
            estimated_file_count=count,
            workspace_default_ignores=_WORKSPACE_DEFAULT_IGNORES,
            repo_ignore_overrides=repo.ignore_overrides,
            effective_ignores=effective,
        )

    async def _fetch_row(self, repo_id: str):
        db = get_db()
        async with db.execute("SELECT * FROM local_repos WHERE id = ?", (repo_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError("LocalRepo", repo_id)
        return row
