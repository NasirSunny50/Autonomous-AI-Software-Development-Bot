"""Cloudflare Pages deployer (free tier).

Great for static / statically-exported sites. Deploys the build output directory
with wrangler. Auth via `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`
environment variables (never CLI args). Yields a `*.pages.dev` URL.
"""
from __future__ import annotations

from pathlib import Path

from app.deploy.base import Deployer, DeployResult, find_build_dir
from app.deploy.vercel import npx


class CloudflarePagesDeployer(Deployer):
    name = "cloudflare"

    def available(self) -> bool:
        return bool(self.settings.cloudflare_api_token
                    and self.settings.cloudflare_account_id)

    async def deploy(self, project_path: Path | str, *, prod: bool = False) -> DeployResult:
        if not self.available():
            return DeployResult(False, "", self.name,
                                error="CLOUDFLARE_API_TOKEN / ACCOUNT_ID not set")
        build_dir = find_build_dir(project_path)
        if not build_dir:
            return DeployResult(False, "", self.name,
                                error="no build output dir found (build first)")
        project_name = Path(project_path).name[:54] or "app"
        args = npx(["wrangler", "pages", "deploy", build_dir,
                    "--project-name", project_name])
        env = {"CLOUDFLARE_API_TOKEN": self.settings.cloudflare_api_token,
               "CLOUDFLARE_ACCOUNT_ID": self.settings.cloudflare_account_id,
               "CI": "1"}
        outcome = await self._run(project_path, args, env, timeout=600)
        return self._result_from(outcome)
