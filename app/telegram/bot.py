"""Telegram interface — the owner's control surface.

Only the configured owner is obeyed. Long-running work runs in the background so
the bot stays responsive. Approvals use inline buttons. A daily digest and an
/ask Q&A let the owner stay hands-off and just check in. Outgoing messages are
scrubbed of anything that looks like a secret.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.orchestrator.core import Orchestrator
from app.security.secrets import redact
from app.state.models import ProjectStatus, TaskStatus
from app.state.store import StateStore
from app.utils.logging import get_logger
from app.utils.text import truncate

log = get_logger("telegram")

HELP = """\
🤖 *AI Dev Bot*

Send a requirement in plain language, or use:

/new — start a new project
/projects — list & switch project
/status — current project & task
/ask — ask about the active project
/test — run the quality gate now
/deploy — deploy to a free host (add `prod` for production)
/logs — recent activity
/retry — retry a failed task
/rollback — revert to last checkpoint
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
        self._digest_task: asyncio.Task | None = None
        self._pending_approvals: dict[str, asyncio.Future] = {}

    # ---- proactive notifier / approval (passed to the orchestrator) ----
    async def notify(self, message: str) -> None:
        if self.app:
            await self._safe_send(self.settings.telegram_allowed_user_id, message)

    async def request_approval(self, text: str) -> bool:
        """Ask the owner to approve an action via inline buttons; wait for a tap."""
        if not self.app:
            return False
        token = uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_approvals[token] = fut
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"ap:{token}:1"),
            InlineKeyboardButton("❌ Reject", callback_data=f"ap:{token}:0"),
        ]])
        await self.app.bot.send_message(
            self.settings.telegram_allowed_user_id,
            f"🔐 *Approval needed*\n\n{redact(text)}", parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, timeout=3600)
        except asyncio.TimeoutError:
            self._pending_approvals.pop(token, None)
            return False

    async def _safe_send(self, chat_id: int, text: str) -> None:
        text = truncate(redact(text))
        try:
            await self.app.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try:
                await self.app.bot.send_message(chat_id, text)
            except Exception as exc:  # pragma: no cover
                log.warning("send failed: %s", exc)

    # ---- auth ----
    def _authorized(self, update: Update) -> bool:
        u = update.effective_user
        return bool(u and u.id == self.settings.telegram_allowed_user_id)

    async def _guard(self, update: Update) -> bool:
        if self._authorized(update):
            return True
        log.warning("Unauthorized access: %s",
                    update.effective_user.id if update.effective_user else "?")
        if update.message:
            await update.message.reply_text("⛔ Not authorized.")
        return False

    # ---- commands ----
    async def cmd_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if self._busy:
            await update.message.reply_text("⏳ A project is running. Use /status.")
            return
        arg = " ".join(context.args) if context.args else ""
        if arg:
            await self._begin_project(arg)
        else:
            self._awaiting_requirement = True
            await update.message.reply_text("📝 What do you want me to build?")

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project. Send a requirement or /new.")
            return
        tasks = await self.store.list_tasks(project.id)
        done = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED.value)
        await update.message.reply_text(
            f"*{project.name}*  `{project.slug}`\nStatus: {project.status}\n"
            f"Tasks: {done}/{len(tasks)}\nWorking: {'yes' if self._busy else 'idle'}",
            parse_mode=ParseMode.MARKDOWN)

    async def cmd_projects(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        projects = await self.store.list_projects()
        if not projects:
            await update.message.reply_text("No projects yet.")
            return
        lines = ["*Projects*"]
        for p in projects:
            lines.append(f"{'▶️' if p.is_active else '  '} `{p.slug}` — {p.name} ({p.status})")
        lines.append("\nSwitch with: /switch <slug>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_switch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        slug = (context.args[0] if context.args else "").strip()
        project = await self.store.get_project_by_slug(slug) if slug else None
        if not project:
            await update.message.reply_text("Usage: /switch <slug> (see /projects)")
            return
        await self.store.set_active_project(project.id)
        await update.message.reply_text(f"▶️ Active project: *{project.name}*",
                                        parse_mode=ParseMode.MARKDOWN)

    async def cmd_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        question = " ".join(context.args) if context.args else ""
        if not question:
            await update.message.reply_text("Usage: /ask <question about the project>")
            return
        await update.message.chat.send_action("typing")
        answer = await self.orchestrator.answer_question(question)
        await update.message.reply_text(truncate(redact(answer), 3500))

    async def cmd_test(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project.")
            return
        await update.message.reply_text("🧪 Running quality gate…")
        gate = await self.orchestrator.run_gate_now(project)
        await update.message.reply_text(gate.summary())

    async def cmd_logs(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        logs = await self.store.recent_logs(project.id if project else None, limit=12)
        await update.message.reply_text(truncate("\n".join(logs) or "No logs yet."))

    async def cmd_autonomy(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.message.reply_text(
            f"Autonomy: *{self.settings.autonomy_level}*\n"
            f"Max retries: {self.settings.max_retries}\n"
            f"Claude caps: {self.settings.claude_max_calls_per_project}/project, "
            f"{self.settings.claude_max_calls_per_day}/day",
            parse_mode=ParseMode.MARKDOWN)

    async def cmd_pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if project:
            self.orchestrator._paused.add(project.id)
            await self.store.update_project_status(project.id, ProjectStatus.PAUSED.value)
        await update.message.reply_text("⏸ Paused. /resume to continue.")

    async def cmd_resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project.")
            return
        self.orchestrator.clear_pause(project.id)
        await self.store.update_project_status(project.id, ProjectStatus.IN_PROGRESS.value)
        await update.message.reply_text("▶️ Resuming…")
        self._run_bg(self.orchestrator.run_project(project))

    async def cmd_retry(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project.")
            return
        tasks = await self.store.list_tasks(project.id)
        stuck = [t for t in tasks if t.status in
                 (TaskStatus.FAILED.value, TaskStatus.NEEDS_REVIEW.value)]
        if not stuck:
            await update.message.reply_text("Nothing to retry.")
            return
        for t in stuck:
            await self.store.update_task(t.id, status=TaskStatus.PENDING.value,
                                         retry_count=0)
        self.orchestrator.clear_pause(project.id)
        await self.store.update_project_status(project.id, ProjectStatus.IN_PROGRESS.value)
        await update.message.reply_text(f"🔁 Retrying {len(stuck)} task(s)…")
        self._run_bg(self.orchestrator.run_project(project))

    async def cmd_deploy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project.")
            return
        prod = bool(context.args) and context.args[0].lower() in ("prod", "production")
        self._run_bg(self.orchestrator.deploy_project(project, prod=prod))

    async def cmd_rollback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        project = await self.store.get_active_project()
        if not project:
            await update.message.reply_text("No active project.")
            return
        ok = await self.orchestrator.rollback_project(project)
        await update.message.reply_text("↩️ Rolled back to last checkpoint."
                                        if ok else "No checkpoint to roll back to.")

    async def cmd_stop(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if self._current_job and not self._current_job.done():
            self._current_job.cancel()
        self._busy = False
        await update.message.reply_text("🛑 Stopped. State saved; nothing corrupted.")

    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        if not self._authorized(update):
            return
        try:
            _, token, val = (q.data or "").split(":")
        except ValueError:
            return
        fut = self._pending_approvals.pop(token, None)
        if fut and not fut.done():
            fut.set_result(val == "1")
        await q.edit_message_text("✅ Approved." if val == "1" else "❌ Rejected.")

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        text = (update.message.text or "").strip()
        if not text:
            return
        if self._busy and not self._awaiting_requirement:
            await update.message.reply_text(
                "⏳ I'm working. Use /status, or /ask <question>.")
            return
        self._awaiting_requirement = False
        await self._begin_project(text)

    # ---- background project launch ----
    async def _begin_project(self, requirement: str) -> None:
        self._busy = True

        async def _run() -> None:
            try:
                await self.orchestrator.handle_requirement(requirement)
            except asyncio.CancelledError:
                await self.notify("🛑 Cancelled.")
                raise
            except Exception as exc:  # pragma: no cover
                log.exception("orchestration error")
                await self.notify(f"⚠️ Unexpected error: `{str(exc)[:200]}`")
            finally:
                self._busy = False

        self._current_job = asyncio.create_task(_run())

    def _run_bg(self, coro) -> None:
        async def _wrap():
            self._busy = True
            try:
                await coro
            except Exception as exc:  # pragma: no cover
                log.exception("bg error")
                await self.notify(f"⚠️ Error: `{str(exc)[:200]}`")
            finally:
                self._busy = False
        self._current_job = asyncio.create_task(_wrap())

    # ---- daily digest (dependency-free scheduler) ----
    async def _daily_digest_loop(self) -> None:
        while True:
            await asyncio.sleep(self._seconds_until_daily())
            try:
                project = await self.store.get_active_project()
                if project:
                    logs = await self.store.recent_logs(project.id, limit=8)
                    tasks = await self.store.list_tasks(project.id)
                    done = sum(1 for t in tasks if t.status == "completed")
                    await self.notify(
                        f"📆 *Daily update*\n\n*{project.name}* ({project.status})\n"
                        f"Tasks: {done}/{len(tasks)}\n\nRecent:\n" + "\n".join(logs))
            except Exception as exc:  # pragma: no cover
                log.warning("digest error: %s", exc)

    def _seconds_until_daily(self) -> float:
        try:
            hh, mm = (int(x) for x in self.settings.daily_update_time.split(":"))
        except ValueError:
            hh, mm = 21, 0
        now = datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _post_init(self, app: Application) -> None:
        await self.store.init()
        # asyncio.create_task (not app.create_task) so PTB doesn't warn about a
        # task made before polling starts; keep a ref so it isn't GC'd.
        self._digest_task = asyncio.create_task(self._daily_digest_loop())
        log.info("bot post-init complete; daily digest scheduled")

    # ---- wiring ----
    def build(self, orchestrator: Orchestrator) -> Application:
        self.orchestrator = orchestrator
        app = (Application.builder()
               .token(self.settings.telegram_bot_token)
               .post_init(self._post_init)
               .build())
        h = app.add_handler
        h(CommandHandler("start", self.cmd_start))
        h(CommandHandler("help", self.cmd_help))
        h(CommandHandler("new", self.cmd_new))
        h(CommandHandler("status", self.cmd_status))
        h(CommandHandler("projects", self.cmd_projects))
        h(CommandHandler("switch", self.cmd_switch))
        h(CommandHandler("ask", self.cmd_ask))
        h(CommandHandler("test", self.cmd_test))
        h(CommandHandler("logs", self.cmd_logs))
        h(CommandHandler("autonomy", self.cmd_autonomy))
        h(CommandHandler("pause", self.cmd_pause))
        h(CommandHandler("resume", self.cmd_resume))
        h(CommandHandler("retry", self.cmd_retry))
        h(CommandHandler("deploy", self.cmd_deploy))
        h(CommandHandler("rollback", self.cmd_rollback))
        h(CommandHandler("stop", self.cmd_stop))
        h(CallbackQueryHandler(self.on_callback, pattern=r"^ap:"))
        h(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app = app
        return app
