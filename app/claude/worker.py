"""Headless Claude Code wrapper — the bot's Brain + Hands.

Python drives Claude Code non-interactively via `claude -p` (print mode). The
prompt is fed on **stdin** to avoid all shell-quoting/newline problems, and the
result is requested as JSON so we can reliably read success/failure instead of
scraping human text.

The exact CLI syntax can vary by installed version, so the command and its flags
are fully configurable (see `.env`). On Windows a `.cmd` shim is invoked through
`cmd /c` because it cannot be exec'd directly.

Guards (uncontrolled-execution protection):
- a hard timeout on every run (from the process runner),
- prompt-size cap (token discipline; oversized prompts are refused),
- the working directory must exist and be a real directory,
- every execution is logged to logs/claude.log.
"""
from __future__ import annotations

import enum
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.claude.report import ClaudeReport, parse_report
from app.utils.logging import get_logger
from app.utils.process import CommandResult, run_command

log = get_logger("claude")

# Refuse absurdly large prompts — keeps token usage bounded and catches bugs
# where the whole project might get stuffed into the prompt.
MAX_PROMPT_CHARS = 60_000


class ClaudeOutcome(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


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
    duration_s: float = 0.0
    report: ClaudeReport | None = None

    @property
    def outcome(self) -> ClaudeOutcome:
        if self.timed_out:
            return ClaudeOutcome.TIMEOUT
        return ClaudeOutcome.SUCCESS if self.ok else ClaudeOutcome.FAILED


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

    def _base_args(self) -> list[str]:
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
        cwd_path = Path(cwd)
        if not cwd_path.is_dir():
            log.error("refusing to run: cwd is not a directory: %s", cwd_path)
            return ClaudeResult(
                ok=False, text="", raw_stdout="", raw_stderr="",
                exit_code=None, timed_out=False,
                error=f"workspace does not exist: {cwd_path}",
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            log.error("refusing to run: prompt too large (%d chars)", len(prompt))
            return ClaudeResult(
                ok=False, text="", raw_stdout="", raw_stderr="",
                exit_code=None, timed_out=False,
                error=f"prompt exceeds {MAX_PROMPT_CHARS} chars (token guard)",
            )

        args = self._base_args()
        log.info("claude start | cwd=%s | prompt=%d chars", cwd_path, len(prompt))
        res = await run_command(args, cwd=cwd_path, timeout=self.timeout,
                                input_text=prompt)
        result = self._parse(res)
        log.info("claude done  | outcome=%s | exit=%s | %.1fs",
                 result.outcome.value, result.exit_code, res.duration_s)
        return result

    def _parse(self, res: CommandResult) -> ClaudeResult:
        if res.timed_out:
            return ClaudeResult(
                ok=False, text="", raw_stdout=res.stdout, raw_stderr=res.stderr,
                exit_code=res.exit_code, timed_out=True, error="claude timed out",
                duration_s=res.duration_s,
            )

        text, session_id, is_error = "", None, False
        parsed_ok = False
        if self.output_format == "json" and res.stdout.strip():
            stripped = res.stdout.strip()
            candidate = stripped if stripped.startswith("{") else stripped.splitlines()[-1]
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, IndexError):
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
            duration_s=res.duration_s,
            report=parse_report(text),
        )
