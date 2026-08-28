"""Deterministic subprocess runner.

This is a core "Commander" primitive: Python — not AI — runs commands, captures
stdout/stderr/exit code, and enforces a timeout. No command is ever assumed to
succeed; callers inspect `CommandResult.ok`. Every long-running external tool
(Claude Code, npm, git, playwright) goes through here.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cwd: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def run_command(
    args: list[str] | str,
    cwd: Path | str | None = None,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> CommandResult:
    """Run a command asynchronously and capture everything.

    `args` may be a list (preferred, no shell) or a string (split with shlex).
    A timeout kills the process tree and returns `timed_out=True` rather than
    hanging — bounded execution is a hard requirement of this system.
    """
    if isinstance(args, str):
        arg_list = shlex.split(args, posix=(os.name != "nt"))
    else:
        arg_list = list(args)

    display = " ".join(arg_list)
    run_env = {**os.environ, **(env or {})}
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *arg_list,
            cwd=str(cwd) if cwd else None,
            env=run_env,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=display, exit_code=127, stdout="", stderr=str(exc),
            duration_s=0.0, cwd=str(cwd or ""),
        )

    stdin_bytes = input_text.encode() if input_text is not None else None
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes), timeout=timeout
        )
    except asyncio.TimeoutError:
        _kill(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
        return CommandResult(
            command=display, exit_code=None, stdout="", stderr="timeout",
            duration_s=time.monotonic() - start, timed_out=True, cwd=str(cwd or ""),
        )

    return CommandResult(
        command=display,
        exit_code=proc.returncode,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
        duration_s=time.monotonic() - start,
        cwd=str(cwd or ""),
    )


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
