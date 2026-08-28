"""Headless Claude Code wrapper — the bot's Brain + Hands.

Python drives Claude Code non-interactively via `claude -p` (print mode). The
prompt is fed on **stdin** to avoid all shell-quoting/newline problems, and the
result is requested as JSON so we can reliably read success/failure instead of
scraping human text.

The exact CLI syntax can vary by installed version, so the command and its flags
are fully configurable (see `.env`). On Windows a `.cmd` shim is invoked through
`cmd /c` because it cannot be exec'd directly.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.utils.process import CommandResult, run_command


@dataclass
class ClaudeResult:
    ok: bool
    text: str                 # Claude's final message / result text
    raw_stdout: str
    raw_stderr: str
    exit_code: int | None
    timed_out: bool
    session_id: str | None = None
    error: str | None = None


class ClaudeWorker:
    def __init__(
        self,
        command: str = "claude",
        extra_args: str = "--dangerously-skip-permissions",
        output_format: str = "json",
        timeout: int = 1800,
    ):
        self.command = command
        self.extra_args = extra_args
        self.output_format = output_format
        self.timeout = timeout

    def _base_args(self, prompt_via_stdin: bool = True) -> list[str]:
        args = [self.command, "-p"]
        if self.output_format:
            args += ["--output-format", self.output_format]
        if self.extra_args:
            args += shlex.split(self.extra_args, posix=(os.name != "nt"))
        # Wrap Windows .cmd/.bat shims through cmd /c so they can be launched.
        if os.name == "nt":
            return ["cmd", "/c", *args]
        return args

    async def available(self) -> bool:
        """Cheap check that the Claude Code CLI is installed & reachable."""
        args = (["cmd", "/c", self.command, "--version"]
                if os.name == "nt" else [self.command, "--version"])
        res = await run_command(args, timeout=30)
        return res.ok

    async def run_task(self, prompt: str, cwd: Path | str) -> ClaudeResult:
        """Send one focused task to Claude Code and capture the structured result.

        `cwd` MUST be the target project's workspace (validated by the caller).
        `prompt` is passed on stdin.
        """
        args = self._base_args()
        res = await run_command(
            args, cwd=cwd, timeout=self.timeout, input_text=prompt
        )
        return self._parse(res)

    def _parse(self, res: CommandResult) -> ClaudeResult:
        if res.timed_out:
            return ClaudeResult(
                ok=False, text="", raw_stdout=res.stdout, raw_stderr=res.stderr,
                exit_code=res.exit_code, timed_out=True, error="claude timed out",
            )

        text, session_id, is_error = "", None, False
        parsed_ok = False
        if self.output_format == "json" and res.stdout.strip():
            try:
                data = json.loads(res.stdout.strip().splitlines()[-1]
                                  if res.stdout.strip().startswith("{") is False
                                  else res.stdout.strip())
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                parsed_ok = True
                text = str(data.get("result", data.get("text", "")))
                session_id = data.get("session_id")
                is_error = bool(data.get("is_error", False))

        if not parsed_ok:
            text = res.stdout.strip()

        ok = res.ok and not is_error
        return ClaudeResult(
            ok=ok,
            text=text,
            raw_stdout=res.stdout,
            raw_stderr=res.stderr,
            exit_code=res.exit_code,
            timed_out=False,
            session_id=session_id,
            error=None if ok else (res.stderr.strip() or "claude reported an error"),
        )
