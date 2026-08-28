"""Select a deployer from settings (deterministic, free-only, opt-in)."""
from __future__ import annotations

from app.config import Settings
from app.deploy.base import Deployer
from app.deploy.cloudflare import CloudflarePagesDeployer
from app.deploy.surge import SurgeDeployer
from app.deploy.vercel import VercelDeployer
from app.testing.commands import ProjectCommandRunner

_REGISTRY = {
    "vercel": VercelDeployer,
    "cloudflare": CloudflarePagesDeployer,
    "surge": SurgeDeployer,
}
# Order used when DEPLOY_PROVIDER=auto.
_AUTO_ORDER = ["vercel", "cloudflare", "surge"]


def build_deployer(settings: Settings, runner: ProjectCommandRunner) -> Deployer | None:
    choice = (settings.deploy_provider or "").strip().lower()
    if not choice:
        return None  # deployment disabled

    if choice == "auto":
        for name in _AUTO_ORDER:
            dep = _REGISTRY[name](settings, runner)
            if dep.available():
                return dep
        return None  # no provider token configured

    cls = _REGISTRY.get(choice)
    if not cls:
        return None
    dep = cls(settings, runner)
    return dep if dep.available() else None
