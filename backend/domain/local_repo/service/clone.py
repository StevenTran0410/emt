"""Clone-from-URL: git clone + register as a local repo."""
import asyncio
import os
import subprocess  # still used by clone_from_url
from pathlib import Path

from infrastructure.db.database import get_db
from shared.errors import ConflictError
from shared.git_utils import read_git_info, run_git
from shared.logger import logger

from ..types import AddLocalRepoRequest, CloneFromUrlRequest, LocalRepo
from .fs_helpers import _detect_source_type, _normalize_repo_path


class CloneMixin:
    async def clone_from_url(self, req: CloneFromUrlRequest) -> LocalRepo:
        """Clone a remote git URL to dest_path, then register it as a local repo."""
        normalized_dest_path = _normalize_repo_path(req.dest_path)
        dest = Path(normalized_dest_path)

        if dest.exists():
            if dest.is_dir() and not any(dest.iterdir()):
                # stale empty folder from previous failed delete/clone
                dest.rmdir()

        if dest.exists():
            # If destination is already a git repo with same remote, reuse it.
            git_info = await read_git_info(str(dest))
            if git_info.get("is_git_repo"):
                remote = await run_git(str(dest), ["remote", "get-url", "origin"], timeout=10)
                if remote and remote.rstrip("/") == req.url.rstrip("/"):
                    logger.info(f"Destination already contains same repo, reusing: {normalized_dest_path}")
                    try:
                        # mode must be threaded through here too: this exact path fires when the same URL is re-cloned under a different mode, and without req.mode it always registered as 'code_analysis' regardless of what was requested.
                        return await self.add(
                            AddLocalRepoRequest(
                                path=normalized_dest_path,
                                workspace_id=req.workspace_id,
                                mode=req.mode,
                            )
                        )
                    except ConflictError:
                        # Already registered in DB for this exact (path, workspace, mode) — return existing row
                        db = get_db()
                        async with db.execute(
                            "SELECT id FROM local_repos WHERE path = ? AND workspace_id IS ? AND mode = ?",
                            (normalized_dest_path, req.workspace_id, req.mode),
                        ) as cur:
                            row = await cur.fetchone()
                        if row:
                            return await self.get_by_id(row["id"])
            raise ConflictError(
                f"Destination '{normalized_dest_path}' already exists. Remove it first or choose another repo URL."
            )

        dest.parent.mkdir(parents=True, exist_ok=True)

        # Build environment — inject GIT_SSH_COMMAND for SSH URLs if a key is configured
        env = os.environ.copy()
        ssh_env = await self._ssh_env_if_needed(req.url)
        if ssh_env:
            env = ssh_env

        def _do_clone() -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "clone", req.url, str(dest)],
                capture_output=True,
                text=True,
                timeout=300,  # 5 min max
                env=env,
            )

        result = await asyncio.to_thread(_do_clone)
        if result.returncode != 0:
            # Filter out the informational "Cloning into '...'" line — only keep actual errors
            lines = [
                ln for ln in (result.stderr or "").splitlines()
                if ln.strip() and not ln.strip().startswith("Cloning into ")
            ]
            msg = "\n".join(lines).strip() or "git clone failed (unknown error)"
            raise ValueError(msg)

        logger.info(f"Cloned '{req.url}' → {normalized_dest_path}")
        return await self.add(
            AddLocalRepoRequest(path=normalized_dest_path, workspace_id=req.workspace_id, mode=req.mode),
            source_type=_detect_source_type(req.url),
        )
