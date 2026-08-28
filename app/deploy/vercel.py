"""Vercel deployer (free hobby tier).

Best general choice: auto-detects the framework (Next.js/Vite/static/etc.),
builds on Vercel's side, and prints a live preview URL. Token is read from the
`VERCEL_TOKEN` environment variable by the CLI — never passed as an argument.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.deploy.base import Deployer, DeployResult


def npx(args: list[str]) -> list[str]:
    return ["cmd", "/c", "npx", *args] if os.name == "nt" else ["npx", *args]


class VercelDeployer(Deployer):
    name = "vercel"

    def available(self) -> bool:
        return bool(self.settings.vercel_token)

    async def deploy(self, project_path: Path | str, *, prod: bool = False) -> DeployResult:
        if not self.available():
            return DeployResult(False, "", self.name, error="VERCEL_TOKEN not set")
        args = npx(["vercel", "deploy", "--yes"])
        if prod:
            args.append("--prod")
        env = {"VERCEL_TOKEN": self.settings.vercel_token, "CI": "1"}
        outcome = await self._run(project_path, args, env, timeout=900)
        return self._result_from(outcome)
