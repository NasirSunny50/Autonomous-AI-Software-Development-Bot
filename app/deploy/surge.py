"""Surge.sh deployer (free, static sites only).

The lowest-friction fallback: publishes a static build directory to a
`<name>.surge.sh` domain. Auth via `SURGE_TOKEN` (and optionally `SURGE_LOGIN`)
environment variables.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.deploy.base import Deployer, DeployResult, find_build_dir
from app.deploy.vercel import npx


class SurgeDeployer(Deployer):
    name = "surge"

    def available(self) -> bool:
        return bool(self.settings.surge_token)

    def _domain(self, project_path: Path | str) -> str:
        slug = re.sub(r"[^a-z0-9-]", "", Path(project_path).name.lower())[:40] or "app"
        return f"{slug}.surge.sh"

    async def deploy(self, project_path: Path | str, *, prod: bool = False) -> DeployResult:
        if not self.available():
            return DeployResult(False, "", self.name, error="SURGE_TOKEN not set")
        build_dir = find_build_dir(project_path)
        if not build_dir:
            return DeployResult(False, "", self.name,
                                error="no build output dir found (build first)")
        domain = self._domain(project_path)
        args = npx(["surge", build_dir, domain])
        env = {"SURGE_TOKEN": self.settings.surge_token}
        if self.settings.surge_login:
            env["SURGE_LOGIN"] = self.settings.surge_login
        outcome = await self._run(project_path, args, env, timeout=300)
        result = self._result_from(outcome)
        # surge often prints the domain without the scheme; synthesize if needed.
        if outcome.ok and not result.url:
            return DeployResult(True, f"https://{domain}", self.name)
        return result
