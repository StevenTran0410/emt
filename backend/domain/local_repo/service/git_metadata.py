"""Git metadata validation, branch listing, and revalidation."""
import asyncio
import os
from pathlib import Path

from infrastructure.db.database import get_db
from shared.git_utils import is_ssh_url, list_branches, read_git_info, run_git
from shared.logger import logger
from shared.utils import utc_now_iso

from ..types import LocalRepo, SetBranchRequest, ValidateFolderRequest, ValidateFolderResponse
from .fs_helpers import _check_size_warning
from .mapping import _row_to_model


class GitMetadataMixin:
    async def _ssh_env_if_needed(self, url: str | None) -> dict | None:
        if not url or not is_ssh_url(url):
            return None
        db = get_db()
        async with db.execute(
            "SELECT value FROM app_metadata WHERE key='git_ssh_key_path'"
        ) as cur:
            row = await cur.fetchone()
        ssh_key = row["value"] if row else None
        if not ssh_key:
            return None
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = (
            f'ssh -i "{ssh_key}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
        )
        return env

    async def validate(self, req: ValidateFolderRequest) -> ValidateFolderResponse:
        p = Path(req.path)
        exists = p.exists()
        is_dir = p.is_dir() if exists else False
        name = p.name or req.path

        if not exists or not is_dir:
            return ValidateFolderResponse(
                path=req.path,
                name=name,
                exists=exists,
                is_directory=is_dir,
                is_git_repo=False,
                git_branch=None,
                git_head_hash=None,
                git_remote_url=None,
                has_size_warning=False,
                size_warning_reason=None,
            )

        git_info, (size_warn, size_reason) = await asyncio.gather(
            read_git_info(req.path),
            _check_size_warning(req.path),
        )

        return ValidateFolderResponse(
            path=req.path,
            name=name,
            exists=True,
            is_directory=True,
            is_git_repo=git_info["is_git_repo"],
            git_branch=git_info["branch"],
            git_head_hash=git_info["head_hash"],
            git_remote_url=git_info["remote_url"],
            has_size_warning=size_warn,
            size_warning_reason=size_reason,
        )

    async def list_branches(self, repo_id: str, refresh: bool = False) -> list[str]:
        """Return branches. Refresh remote refs only when explicitly requested."""
        repo = await self.get_by_id(repo_id)
        if not repo.is_git_repo:
            return []

        if refresh:
            # Optional on-demand refresh to avoid slow branch dropdown open.
            env = await self._ssh_env_if_needed(repo.git_remote_url)
            await run_git(repo.path, ["fetch", "--all", "--prune"], env=env, timeout=15)

        return await list_branches(repo.path)

    async def set_branch(self, repo_id: str, req: SetBranchRequest) -> LocalRepo:
        """Persist the user's chosen analysis branch."""
        existing = await self.get_by_id(repo_id)
        if not existing.is_git_repo:
            raise ValueError("Cannot set branch on a non-git folder")

        # Verify the branch actually exists locally
        branches = await list_branches(existing.path)
        if branches and req.branch not in branches:
            raise ValueError(
                f"Branch '{req.branch}' not found in local repo. "
                f"Available: {', '.join(branches[:10])}"
            )

        db = get_db()
        await db.execute(
            "UPDATE local_repos SET selected_branch=? WHERE id=?",
            (req.branch, repo_id),
        )
        await db.commit()
        logger.info(f"Set analysis branch to '{req.branch}' for repo {repo_id}")
        return await self.get_by_id(repo_id)

    async def revalidate(self, repo_id: str) -> LocalRepo:
        """Refresh git metadata for an existing local repo."""
        existing = await self.get_by_id(repo_id)
        validation = await self.validate(ValidateFolderRequest(path=existing.path))
        now = utc_now_iso()
        db = get_db()
        await db.execute(
            """UPDATE local_repos
               SET is_git_repo=?, git_branch=?, git_head_hash=?, git_remote_url=?,
                   has_size_warning=?, last_validated_at=?
               WHERE id=?""",
            (
                int(validation.is_git_repo),
                validation.git_branch,
                validation.git_head_hash,
                validation.git_remote_url,
                int(validation.has_size_warning),
                now,
                repo_id,
            ),
        )
        await db.commit()
        row = await self._fetch_row(repo_id)
        return _row_to_model(row)
