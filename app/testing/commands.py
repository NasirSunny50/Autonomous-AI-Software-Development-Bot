"""Safe project command execution — the deterministic hands of the Commander.

Every shell command the bot runs inside a project (build, test, lint, dev server)
goes through here so that, before anything executes:
  1. the working directory is validated to live inside the managed workspaces,
  2. destructive commands are blocked (they require explicit owner approval),
  3. a timeout is enforced and the run is logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.security.guard import first_destructive_match, is_destructive, validate_workspace
from app.utils.logging import get_logger
from app.utils.process import CommandResult, run_command

log = get_logger("system")


@dataclass
class RunOutcome:
    ok: bool
    blocked: bool                     # blocked by the destructive-command guard
    skipped: bool                     # not applicable to this project
    name: str
    result: CommandResult | None = None
    reason: str = ""

    @property
    def stdout(self) -> str:
        return self.result.stdout if self.result else ""

    @property
    def stderr(self) -> str:
        return self.result.stderr if self.result else ""


class ProjectCommandRunner:
    def __init__(self, workspaces_root: Path | str):
        self.workspaces_root = Path(workspaces_root)

    async def run(self, project_path: Path | str, args: list[str], *, name: str,
                  timeout: float = 300.0, allow_destructive: bool = False,
                  env: dict[str, str] | None = None) -> RunOutcome:
        # 1. workspace containment
        validate_workspace(project_path, self.workspaces_root)

        # 2. destructive-command guard
        command_str = " ".join(args)
        if is_destructive(command_str) and not allow_destructive:
            match = first_destructive_match(command_str)
            log.warning("blocked destructive command (%s): %s", name, command_str)
            return RunOutcome(ok=False, blocked=True, skipped=False, name=name,
                              reason=f"destructive command requires approval: {match}")

        # 3. bounded, logged execution (secrets passed via env are never logged)
        res = await run_command(args, cwd=project_path, timeout=timeout, env=env)
        log.info("cmd %-10s | ok=%s | exit=%s | %.1fs", name, res.ok, res.exit_code,
                 res.duration_s)
        return RunOutcome(ok=res.ok, blocked=False, skipped=False, name=name, result=res)

    def skipped(self, name: str, reason: str) -> RunOutcome:
        return RunOutcome(ok=True, blocked=False, skipped=True, name=name, reason=reason)
