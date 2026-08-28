"""Parse the structured report Claude Code returns at the end of a task.

Prompts ask Claude to end with:

    CHANGED_FILES: a.py, b.tsx
    BUILD: pass|fail|not-run
    TESTS: pass|fail|not-run
    SUMMARY: one or two sentences

This is ADVISORY only — the orchestrator never marks a task complete just because
Claude says so; the deterministic quality gate (Phase 7) is the real authority.
The report is used for logging, Telegram summaries, and to hint the debugger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_STATUS_VALUES = {"pass", "fail", "not-run", "notrun", "unknown"}


@dataclass
class ClaudeReport:
    changed_files: list[str] = field(default_factory=list)
    build: str = "unknown"
    tests: str = "unknown"
    summary: str = ""
    raw: str = ""

    @property
    def claims_success(self) -> bool:
        """Claude's own (untrusted) opinion that nothing failed."""
        return self.build != "fail" and self.tests != "fail"


def _norm_status(value: str) -> str:
    v = value.strip().lower()
    v = v.replace("not run", "not-run")
    if v not in _STATUS_VALUES:
        # keep only the leading token, e.g. "pass (42 tests)" -> "pass"
        head = re.split(r"[\s(]", v, maxsplit=1)[0]
        v = head if head in _STATUS_VALUES else "unknown"
    return "not-run" if v == "notrun" else v


def parse_report(text: str) -> ClaudeReport:
    report = ClaudeReport(raw=text or "")
    if not text:
        return report

    for line in text.splitlines():
        m = re.match(r"\s*(CHANGED_FILES|BUILD|TESTS|SUMMARY)\s*:\s*(.*)$",
                     line, re.IGNORECASE)
        if not m:
            continue
        key, val = m.group(1).upper(), m.group(2).strip()
        if key == "CHANGED_FILES":
            report.changed_files = [f.strip() for f in re.split(r"[,\n]", val)
                                    if f.strip() and f.strip().lower() not in
                                    ("none", "n/a", "-")]
        elif key == "BUILD":
            report.build = _norm_status(val)
        elif key == "TESTS":
            report.tests = _norm_status(val)
        elif key == "SUMMARY":
            report.summary = val
    return report
