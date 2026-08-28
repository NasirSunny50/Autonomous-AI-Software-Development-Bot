"""Git safety layer.

Checkpoints before major tasks, commits on success, rollback on repeated
failure. Changed files are detected with `git diff` / `git status` — never by
parsing AI output. Destructive operations (force-push, branch delete) are NOT
implemented here on purpose; they must never happen automatically.
"""
from __future__ import annotations

from pathlib import Path

from app.utils.process import run_command

_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}


class GitRepo:
    def __init__(self, path: Path | str, author_name: str = "AI Dev Bot",
                 author_email: str = "aidevbot@localhost"):
        self.path = Path(path)
        self.author_name = author_name
        self.author_email = author_email

    async def _git(self, *args: str, timeout: float = 60):
        cmd = [
            "git",
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
            *args,
        ]
        return await run_command(cmd, cwd=self.path, timeout=timeout, env=_GIT_ENV)

    async def is_repo(self) -> bool:
        res = await self._git("rev-parse", "--is-inside-work-tree")
        return res.ok and res.stdout.strip() == "true"

    async def ensure_repo(self) -> None:
        """Initialize the repo and make an initial commit if needed."""
        self.path.mkdir(parents=True, exist_ok=True)
        if not await self.is_repo():
            await self._git("init")
        # Ensure at least one commit exists so rollback/diff have a base.
        head = await self._git("rev-parse", "HEAD")
        if not head.ok:
            await self._git("add", "-A")
            await self._git("commit", "--allow-empty", "-m", "chore: initial commit")

    async def changed_files(self) -> list[str]:
        """Files changed vs HEAD (staged, unstaged, and untracked)."""
        res = await self._git("status", "--porcelain")
        if not res.ok:
            return []
        files = []
        for line in res.stdout.splitlines():
            if len(line) > 3:
                files.append(line[3:].strip().strip('"'))
        return files

    async def has_changes(self) -> bool:
        return len(await self.changed_files()) > 0

    async def current_head(self) -> str | None:
        res = await self._git("rev-parse", "HEAD")
        return res.stdout.strip() if res.ok else None

    async def checkpoint(self, label: str) -> str | None:
        """Commit any pending changes as a checkpoint. Returns the commit hash.

        If there is nothing to commit, returns the current HEAD (still a valid
        rollback target)."""
        if await self.has_changes():
            await self._git("add", "-A")
            res = await self._git("commit", "-m", f"checkpoint: {label}")
            if not res.ok and "nothing to commit" not in (res.stdout + res.stderr):
                return None
        return await self.current_head()

    async def commit_all(self, message: str) -> str | None:
        if not await self.has_changes():
            return await self.current_head()
        await self._git("add", "-A")
        res = await self._git("commit", "-m", message)
        if not res.ok:
            return None
        return await self.current_head()

    async def rollback(self, commit_hash: str) -> bool:
        """Hard reset to a known-good checkpoint. Non-destructive to history
        (no force-push, no branch deletion)."""
        if not commit_hash:
            return False
        res = await self._git("reset", "--hard", commit_hash)
        return res.ok

    async def short_diff(self, max_lines: int = 200) -> str:
        """A trimmed diff for evidence collection (never the whole project)."""
        res = await self._git("diff", "HEAD", "--stat")
        return "\n".join(res.stdout.splitlines()[:max_lines]) if res.ok else ""
