"""Typed configuration, loaded from `.env` (never hard-coded).

The only paid dependency permitted anywhere in this system is Claude Code.
Every other provider must use a free tier. These settings expose the knobs
that enforce that rule (budgets, autonomy, retries).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the folder that contains this `app/` package.
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All runtime configuration. Field names map case-insensitively to the
    UPPERCASE variables in `.env` (see `.env.example`)."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Telegram ----
    telegram_bot_token: str = ""
    telegram_allowed_user_id: int = 0

    # ---- Claude Code (the only paid worker) ----
    claude_command: str = "claude"
    claude_plan: Literal["pro", "max"] = "pro"
    claude_max_calls_per_project: int = 40
    claude_max_calls_per_day: int = 150
    claude_timeout_seconds: int = 1800

    # ---- Free AI providers (cheap glue only) ----
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_api_key: str = ""
    kilo_api_key: str = ""
    mistral_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    ollama_model: str = "gpt-oss:120b"
    ollama_base_url: str = "https://ollama.com/v1"
    kilo_model: str = "kilo-auto/free"   # FREE only (gateway also has paid models)
    kilo_base_url: str = "https://api.kilo.ai/api/gateway"
    mistral_model: str = "mistral-small-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    # ---- Behaviour ----
    autonomy_level: Literal["low", "medium", "high"] = "high"
    max_retries: int = 3
    daily_update_time: str = "21:00"

    # ---- Auto-deploy (free hosts; opt-in) ----
    # Provider: "" (disabled) | auto | vercel | cloudflare | surge.
    # "auto" picks the first provider whose token is configured.
    deploy_provider: str = ""
    deploy_prod: bool = False           # False = preview deploy (safer default)
    vercel_token: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    surge_token: str = ""
    surge_login: str = ""               # email for surge, if required

    # ---- Paths (relative values are resolved against ROOT_DIR) ----
    workspaces_dir: str = "workspaces"
    state_db_path: str = "data/state.db"
    logs_dir: str = "logs"

    # ---- Derived absolute paths ----
    @property
    def workspaces_path(self) -> Path:
        return self._abs(self.workspaces_dir)

    @property
    def db_path(self) -> Path:
        return self._abs(self.state_db_path)

    @property
    def logs_path(self) -> Path:
        return self._abs(self.logs_dir)

    def _abs(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (ROOT_DIR / p)

    # ---- Validation helpers (deterministic, no AI) ----
    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token) and self.telegram_allowed_user_id > 0

    def free_providers_configured(self) -> list[str]:
        names = []
        if self.gemini_api_key:
            names.append("gemini")
        if self.groq_api_key:
            names.append("groq")
        if self.openrouter_api_key:
            names.append("openrouter")
        if self.ollama_api_key:
            names.append("ollama")
        if self.kilo_api_key:
            names.append("kilo")
        if self.mistral_api_key:
            names.append("mistral")
        return names

    def ensure_dirs(self) -> None:
        """Create the runtime directories the bot writes to."""
        self.workspaces_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Return the process-wide Settings singleton."""
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings
