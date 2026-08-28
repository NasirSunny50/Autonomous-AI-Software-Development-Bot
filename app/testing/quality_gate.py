"""Deterministic quality gate — the real authority on task completion.

A task is NOT done because Claude Code says so; it is done when the gate passes.
The gate detects the project type and runs the applicable checks (build, tests,
lint, typecheck, and — when wired — browser QA), then returns a structured
verdict. All of this is plain Python: no AI, no tokens.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.testing.commands import ProjectCommandRunner, RunOutcome
from app.utils.logging import get_logger
from app.utils.text import truncate

log = get_logger("system")


def shell(args: list[str]) -> list[str]:
    """On Windows, launch node/tsc `.cmd` shims through `cmd /c`."""
    if os.name == "nt" and args and args[0] in {"npm", "npx", "pnpm", "yarn", "tsc"}:
        return ["cmd", "/c", *args]
    return args


@dataclass
class CheckResult:
    name: str
    status: str            # pass | fail | skip | blocked
    detail: str = ""
    output: str = ""

    @property
    def failed(self) -> bool:
        return self.status in ("fail", "blocked")


@dataclass
class GateResult:
    checks: list[CheckResult] = field(default_factory=list)
    project_type: str = "unknown"

    @property
    def passed(self) -> bool:
        return all(not c.failed for c in self.checks) and bool(self.checks)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.failed]

    def summary(self) -> str:
        icon = {"pass": "✅", "fail": "❌", "skip": "⏭", "blocked": "🚫"}
        return "\n".join(f"{icon.get(c.status, '?')} {c.name.upper():10} {c.status}"
                         for c in self.checks)

    def evidence(self, max_chars: int = 3000) -> str:
        """Trimmed failing output for the debugger — never the whole project."""
        parts = []
        for c in self.failures():
            parts.append(f"### {c.name} failed\n{c.detail}\n{c.output}")
        return truncate("\n\n".join(parts), max_chars)


class QualityGate:
    def __init__(self, runner: ProjectCommandRunner):
        self.runner = runner

    def detect(self, project_path: Path) -> dict:
        p = Path(project_path)
        info: dict = {"type": "unknown", "pm": "npm", "scripts": {}, "tsconfig": False}
        pkg = p / "package.json"
        if pkg.exists():
            info["type"] = "node"
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                info["scripts"] = data.get("scripts", {}) or {}
            except (json.JSONDecodeError, OSError):
                info["scripts"] = {}
            if (p / "pnpm-lock.yaml").exists():
                info["pm"] = "pnpm"
            elif (p / "yarn.lock").exists():
                info["pm"] = "yarn"
            info["tsconfig"] = (p / "tsconfig.json").exists()
            info["node_modules"] = (p / "node_modules").exists()
        elif (p / "pyproject.toml").exists() or (p / "requirements.txt").exists() \
                or any(p.glob("*.py")):
            info["type"] = "python"
        return info

    async def run(self, project_path: Path | str, browser=None) -> GateResult:
        path = Path(project_path)
        info = self.detect(path)
        result = GateResult(project_type=info["type"])
        log.info("quality gate | type=%s | %s", info["type"], path)

        if info["type"] == "node":
            await self._run_node(path, info, result)
        elif info["type"] == "python":
            await self._run_python(path, result)
        else:
            result.checks.append(CheckResult("build", "skip", "unknown project type"))

        if browser is not None:
            await self._run_browser(path, info, browser, result)
        return result

    # ---- node ----
    async def _run_node(self, path: Path, info: dict, result: GateResult) -> None:
        pm, scripts = info["pm"], info["scripts"]

        if not info.get("node_modules"):
            out = await self.runner.run(path, shell([pm, "install"]),
                                        name="install", timeout=600)
            result.checks.append(self._check("install", out))
            if not out.ok:
                return  # nothing else can run without deps

        if "build" in scripts:
            out = await self.runner.run(path, shell([pm, "run", "build"]),
                                        name="build", timeout=600)
            result.checks.append(self._check("build", out))
        else:
            result.checks.append(CheckResult("build", "skip", "no build script"))

        if "test" in scripts:
            out = await self.runner.run(path, shell([pm, "run", "test"]),
                                        name="test", timeout=600,
                                        )
            result.checks.append(self._check("test", out))
        else:
            result.checks.append(CheckResult("test", "skip", "no test script"))

        if "lint" in scripts:
            out = await self.runner.run(path, shell([pm, "run", "lint"]),
                                        name="lint", timeout=300)
            result.checks.append(self._check("lint", out))
        else:
            result.checks.append(CheckResult("lint", "skip", "no lint script"))

        if info.get("tsconfig"):
            out = await self.runner.run(path, shell(["npx", "tsc", "--noEmit"]),
                                        name="typecheck", timeout=300)
            result.checks.append(self._check("typecheck", out))

    # ---- python ----
    async def _run_python(self, path: Path, result: GateResult) -> None:
        import sys
        py = sys.executable
        out = await self.runner.run(path, [py, "-m", "compileall", "-q", "."],
                                    name="build", timeout=120)
        result.checks.append(self._check("build", out))

        has_tests = (path / "tests").is_dir() or any(path.glob("test_*.py")) \
            or any(path.glob("*_test.py"))
        if has_tests:
            out = await self.runner.run(path, [py, "-m", "pytest", "-q"],
                                        name="test", timeout=300)
            result.checks.append(self._check("test", out))
        else:
            result.checks.append(CheckResult("test", "skip", "no tests found"))

    # ---- browser ----
    async def _run_browser(self, path: Path, info: dict, browser, result: GateResult) -> None:
        try:
            br = await browser.smoke_test(path, info)
            result.checks.append(br)
        except Exception as exc:  # never let browser QA crash the gate
            log.warning("browser QA error: %s", exc)
            result.checks.append(CheckResult("browser", "skip", f"error: {exc}"))

    # ---- helpers ----
    @staticmethod
    def _check(name: str, out: RunOutcome) -> CheckResult:
        if out.blocked:
            return CheckResult(name, "blocked", out.reason)
        if out.skipped:
            return CheckResult(name, "skip", out.reason)
        status = "pass" if out.ok else "fail"
        detail = "" if out.ok else f"exit={out.result.exit_code if out.result else '?'}"
        output = "" if out.ok else truncate((out.stdout + "\n" + out.stderr).strip(), 1500)
        return CheckResult(name, status, detail, output)
