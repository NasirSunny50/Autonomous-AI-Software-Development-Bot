"""Deployer abstraction for FREE hosting providers.

Mirrors the AI-provider design: thin, deterministic, free-only, and opt-in. A
deployer runs the host's CLI (via `npx`, so nothing is installed globally) inside
the validated project workspace, passing the auth token through the ENVIRONMENT
only — never as a command argument — so tokens never appear in logs.

Deployment publishes PUBLIC content, so the orchestrator gates it behind the
autonomy/approval policy before ever calling `deploy()`.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.testing.commands import ProjectCommandRunner

# Match the deployment URL a provider CLI prints (last one wins).
_URL_RE = re.compile(r"https://[a-zA-Z0-9._\-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?")

# Common static build output directories, in priority order.
_BUILD_DIRS = ["dist", "build", "out", ".output/public", ".vercel/output/static",
               "public"]


def find_build_dir(project_path: Path | str) -> str | None:
    root = Path(project_path)
    for d in _BUILD_DIRS:
        if (root / d).is_dir():
            return d
    return None


def extract_url(text: str) -> str:
    urls = _URL_RE.findall(text or "")
    return urls[-1] if urls else ""


@dataclass
class DeployResult:
    ok: bool
    url: str
    provider: str
    error: str = ""
    output: str = ""


class Deployer(ABC):
    name: str = "base"

    def __init__(self, settings, runner: ProjectCommandRunner):
        self.settings = settings
        self.runner = runner

    @abstractmethod
    def available(self) -> bool:
        """True only if this provider's free-tier token is configured."""

    @abstractmethod
    async def deploy(self, project_path: Path | str, *, prod: bool = False) -> DeployResult:
        ...

    # ---- shared helpers ----
    async def _run(self, project_path, args, env, *, timeout=600):
        return await self.runner.run(project_path, args, name=f"deploy:{self.name}",
                                     timeout=timeout, env=env)

    def _result_from(self, outcome) -> DeployResult:
        if outcome.blocked:
            return DeployResult(False, "", self.name, error=outcome.reason)
        text = (outcome.stdout + "\n" + outcome.stderr) if outcome.result else ""
        url = extract_url(text)
        if outcome.ok and url:
            return DeployResult(True, url, self.name, output=text[-500:])
        return DeployResult(False, url, self.name,
                            error=("deploy failed (no URL)" if outcome.ok else "cli error"),
                            output=text[-800:])
