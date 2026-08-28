"""Telegram interface — the owner's control surface.

Only the configured owner (TELEGRAM_ALLOWED_USER_ID) is obeyed; every other user
is rejected. Long-running work (Claude Code) runs as a background task so the bot
stays responsive to /status, /pause, etc. Messages are kept concise; internal
reasoning is never forwarded.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.orchestrator.core import Orchestrator
from app.state.models import ProjectStatus
from app.state.store import StateStore
from app.utils.logging import get_logger
from app.utils.text import truncate

log = get_logger("telegram")

HELP = """\
🤖 *AI Dev Bot*

Send me a requirement in plain language, or use:

/new — start a new project
/projects — list & switch project
/status — current project & task
/logs — recent activity
/autonomy — view autonomy level
/pause · /resume · /stop — control execution
/help — this message

Example:
_Build a modern e-commerce site with auth, product listing, cart and checkout._
"""


class TelegramBot:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.app: Application | None = None
        self.orchestrator: Orchestrator | None = None
        self._busy = False
        self._awaiting_requirement = False
        self._current_job: asyncio.Task | None = None

    # ---- proactive notifier passed to the orchestrator ----
    async def notify(self, message: str) -> None:
        if not self.app:
            return
        await self._safe_send(self.settings.telegram_allowed_user_id, message)

    async def _safe_send(self, chat_id: int, text: str) -> None:
        text = truncate(text)
        try:
            await self.app.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            # Markdown parse issues shouldn't drop the message — send plain.
            try:
                await self.app.bot.send_message(chat_id, text)
            except Exception as exc:  # pragma: no cover - network
                log.warning("send failed: %s", exc)

    # ---- auth ----
    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        return bool(user and user.id == self.settings.telegram_allowed_user_id)

    async def _reject(self, update: Update) -> None:
        log.warning("Unauthorized access from user id=%s",
                    update.effective_user.id if update.effective_user else "?")
        if update.message:
            await update.message.reply_text("⛔ Not authorized.")

    # ---- command handlers ----
    async def cmd_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        if self._busy:
            return await update.message.reply_text(
                "⏳ A project is already running. Use /status, or wait for it to finish.")
        arg = " ".join(context.args) if context.args else ""
        if arg:
            await self._begin_project(arg)
        else:
            self._awaiting_requirement = True
            await update.message.reply_text("📝 What do you want me to build?")

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        project = await self.store.get_active_project()
        if not project:
            return await update.message.reply_text("No active project. Send a requirement or /new.")
        tasks = await self.store.list_tasks(project.id)
        done = sum(1 for t in tasks if t.status == "completed")
        lines = [
            f"*{project.name}*  `{project.slug}`",
            f"Status: {project.status}",
            f"Tasks: {done}/{len(tasks)} completed",
            f"Working: {'yes' if self._busy else 'idle'}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_projects(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        projects = await self.store.list_projects()
        if not projects:
            return await update.message.reply_text("No projects yet. Send a requirement or /new.")
        lines = ["*Projects*"]
        for p in projects:
            mark = "▶️" if p.is_active else "  "
            lines.append(f"{mark} `{p.slug}` — {p.name} ({p.status})")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_logs(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        project = await self.store.get_active_project()
        logs = await self.store.recent_logs(project.id if project else None, limit=12)
        text = "\n".join(logs) if logs else "No logs yet."
        await update.message.reply_text(truncate(text))

    async def cmd_autonomy(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        await update.message.reply_text(
            f"Autonomy level: *{self.settings.autonomy_level}*\n"
            f"Max retries: {self.settings.max_retries}\n"
            f"Claude caps: {self.settings.claude_max_calls_per_project}/project, "
            f"{self.settings.claude_max_calls_per_day}/day",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        project = await self.store.get_active_project()
        if project:
            await self.store.update_project_status(project.id, ProjectStatus.PAUSED.value)
        await update.message.reply_text("⏸ Paused. No new tasks will start. /resume to continue.")

    async def cmd_resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        await update.message.reply_text(
            "▶️ Resume acknowledged. (Full task-queue resume lands in Phase 3.)")

    async def cmd_stop(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        if self._current_job and not self._current_job.done():
            self._current_job.cancel()
        self._busy = False
        await update.message.reply_text("🛑 Stopped. State is saved; nothing was corrupted.")

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return await self._reject(update)
        text = (update.message.text or "").strip()
        if not text:
            return
        if self._busy and not self._awaiting_requirement:
            return await update.message.reply_text(
                "⏳ I'm working on a project. Use /status to check progress.")
        self._awaiting_requirement = False
        await self._begin_project(text)

    # ---- project launch (background) ----
    async def _begin_project(self, requirement: str) -> None:
        self._busy = True

        async def _run() -> None:
            try:
                await self.orchestrator.handle_requirement(requirement)
            except asyncio.CancelledError:
                await self.notify("🛑 Current task cancelled.")
                raise
            except Exception as exc:  # pragma: no cover
                log.exception("orchestration error")
                await self.notify(f"⚠️ Unexpected error: `{str(exc)[:200]}`")
            finally:
                self._busy = False

        self._current_job = asyncio.create_task(_run())

    # ---- wiring ----
    def build(self, orchestrator: Orchestrator) -> Application:
        self.orchestrator = orchestrator
        app = Application.builder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("projects", self.cmd_projects))
        app.add_handler(CommandHandler("logs", self.cmd_logs))
        app.add_handler(CommandHandler("autonomy", self.cmd_autonomy))
        app.add_handler(CommandHandler("pause", self.cmd_pause))
        app.add_handler(CommandHandler("resume", self.cmd_resume))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app = app
        return app
