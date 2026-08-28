"""AI Dev Bot entrypoint.

    python run.py

Loads config from .env, initializes state, wires the orchestrator + Claude Code
worker to the Telegram interface, and starts polling. Fails fast with a helpful
message if required configuration is missing.
"""
from __future__ import annotations

import sys

from app.ai.factory import build_router
from app.ai.glue import GlueAI
from app.browser.playwright_qa import BrowserQA, playwright_available
from app.claude.worker import ClaudeWorker
from app.config import get_settings
from app.orchestrator.core import Orchestrator
from app.state.store import StateStore
from app.telegram.bot import TelegramBot
from app.utils.logging import get_logger, init_logging


def main() -> int:
    settings = get_settings()
    settings.ensure_dirs()
    init_logging(settings.logs_path)
    log = get_logger("system")

    print("=" * 60)
    print(" AI Dev Bot")
    print("=" * 60)
    print(f" Autonomy         : {settings.autonomy_level}")
    print(f" Max retries      : {settings.max_retries}")
    print(f" Claude command   : {settings.claude_command}")
    print(f" Claude caps      : {settings.claude_max_calls_per_project}/project, "
          f"{settings.claude_max_calls_per_day}/day")
    print(f" Free providers   : {settings.free_providers_configured() or 'none configured'}")
    print(f" Workspaces       : {settings.workspaces_path}")
    print(f" State DB         : {settings.db_path}")
    print("=" * 60)

    if not settings.telegram_ready():
        print("\n❌ Telegram is not configured.")
        print("   Copy .env.example to .env and set:")
        print("     TELEGRAM_BOT_TOKEN=...      (from @BotFather)")
        print("     TELEGRAM_ALLOWED_USER_ID=.. (your numeric id, from @userinfobot)")
        print("     CLAUDE_COMMAND=claude       (install @anthropic-ai/claude-code)")
        return 1

    store = StateStore(settings.db_path)   # tables created in bot post-init

    worker = ClaudeWorker(
        command=settings.claude_command,
        timeout=settings.claude_timeout_seconds,
    )
    router = build_router(settings)
    glue = GlueAI(router)
    log.info("Free providers configured: %s", router.configured() or "none")

    browser = BrowserQA(settings.logs_path) if playwright_available() else None
    print(f" Browser QA       : {'playwright ready' if browser else 'disabled (no playwright)'}")

    bot = TelegramBot(settings, store)
    orchestrator = Orchestrator(
        settings, store, worker, notify=bot.notify, glue=glue,
        browser=browser, request_approval=bot.request_approval,
    )
    application = bot.build(orchestrator)

    log.info("Bot starting (polling)…")
    print("\n✅ Bot is running. Talk to it on Telegram. Ctrl+C to stop.\n")
    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
