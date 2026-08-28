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
from app.ai.conversation import ConversationEngine
from app.utils.logging import get_logger
from app.utils.text import derive_project_name, truncate

log = get_logger("telegram")

HELP = """\
🤖 *AI Dev Bot*

Just *talk to me normally* — no commands needed. For example:
• _"Ekta ecommerce site banao"_ → I'll name it & start
• _"kdur holo?"_ / _"ki obostha?"_ → progress update
• _"login page ta ki hoise?"_ → I'll answer about the project
• _"onno project e kaj koro"_ → switch project

When you ask me to build something, I'll confirm the name first, then go.

Handy commands (optional):
/workdir — set the folder to build the next project in
/projects · /switch — list / switch project
/status · /logs — progress & recent activity
/test · /retry · /rollback · /deploy — QA, retry, revert, deploy
/pause · /resume · /stop — control execution
/help — this message
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
        self._pending_workdir: str | None = None
        self._pending_project: dict | None = None
        self.conversation: ConversationEngine | None = None
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

    async def cmd_workdir(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        path = " ".join(context.args).strip().strip('"') if context.args else ""
        if not path:
            cur = self._pending_workdir or "(default: bot's workspaces/ folder)"
            await update.message.reply_text(
                f"Current target folder for the next project:\n`{cur}`\n\n"
                "Set one with:\n/workdir F:\\path\\to\\your\\folder",
                parse_mode=ParseMode.MARKDOWN)
            return
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            await update.message.reply_text("⚠️ Please give an absolute path "
                                            "(e.g. F:\\Personal_Passive_Income\\Rooftop Cricket).")
            return
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            await update.message.reply_text(f"❌ Can't use that folder: {exc}")
            return
        self._pending_workdir = str(p)
        await update.message.reply_text(
            f"✅ Next project will be built in:\n`{p}`\n\nNow send the requirement.",
            parse_mode=ParseMode.MARKDOWN)

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
        # Just came from /new -> the message IS the requirement.
        if self._awaiting_requirement:
            self._awaiting_requirement = False
            await self._handle_intent(update, text, "new_project", "", "")
            return
        # Natural conversation: understand intent, then act.
        await update.message.chat.send_action("typing")
        ctx = await self._chat_context()
        result = await self.conversation.interpret(text, ctx)
        await self._handle_intent(update, text, result["intent"],
                                  result.get("project_name", ""), result.get("reply", ""))

    async def _handle_intent(self, update: Update, text: str, intent: str,
                             project_name: str, reply: str) -> None:
        if intent == "new_project":
            if self._busy:
                await update.message.reply_text(
                    "⏳ Ekhon ekta project cholche. Shesh hole notun ta dhorbo — "
                    "majhe status jante 'kdur holo?' likho.")
                return
            name = project_name or derive_project_name(text)
            self._pending_project = {"requirement": text, "name": name,
                                     "target_dir": self._pending_workdir}
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Start", callback_data="pj:start"),
                InlineKeyboardButton("❌ Cancel", callback_data="pj:cancel"),
            ]])
            folder = f"\n📁 `{self._pending_workdir}`" if self._pending_workdir else ""
            head = (reply + "\n\n") if reply else ""
            await update.message.reply_text(
                f"{head}🏗️ Project: *{name}*{folder}\n\nStart building? "
                "(ba onno naam bolte paro)",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        elif intent == "confirm":
            if self._pending_project and not self._busy:
                await self._start_pending(update.message.reply_text)
            else:
                await update.message.reply_text(
                    reply or "Ekhon confirm korar kichu nei. Ki banate chao? 🙂")
        elif intent == "cancel":
            self._pending_project = None
            await update.message.reply_text(reply or "Thik ache. 🙂")
        elif intent == "status":
            await self.cmd_status(update, None)
        elif intent == "switch_project":
            if not await self._try_switch(update, text + " " + project_name):
                await update.message.reply_text(
                    reply or "Kon project e jabo? Naam bolo, ba 'project list' likho.")
        elif intent == "question":
            answer = await self.orchestrator.answer_question(text)
            await update.message.reply_text(truncate(redact(answer), 3500))
        elif intent == "help":
            await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)
        else:  # chitchat
            await update.message.reply_text(reply or "🙂")

    async def _try_switch(self, update: Update, text: str) -> bool:
        low = text.lower()
        for p in await self.store.list_projects():
            if p.slug in low or (p.name and p.name.lower() in low):
                await self.store.set_active_project(p.id)
                await update.message.reply_text(
                    f"▶️ Ekhon active: *{p.name}*", parse_mode=ParseMode.MARKDOWN)
                return True
        return False

    async def _start_pending(self, reply_fn) -> None:
        p = self._pending_project
        self._pending_project = None
        self._pending_workdir = None
        await reply_fn(f"🚀 Shuru korchi: {p['name']}")
        await self._begin_project(p["requirement"], name=p["name"],
                                  target_dir=p["target_dir"])

    async def _chat_context(self) -> dict:
        project = await self.store.get_active_project()
        projects = await self.store.list_projects()
        ctx: dict = {"busy": self._busy, "projects": [p.name for p in projects][:10]}
        if project:
            tasks = await self.store.list_tasks(project.id)
            done = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED.value)
            ctx.update(active_project=project.name, status=project.status,
                       tasks_done=done, tasks_total=len(tasks))
        return ctx

    async def on_project_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        if not self._authorized(update):
            return
        action = (q.data or "pj:").split(":", 1)[1]
        if action == "start":
            if self._pending_project and not self._busy:
                p = self._pending_project
                self._pending_project = None
                self._pending_workdir = None
                await q.edit_message_text(f"🚀 Shuru korchi: {p['name']}")
                await self._begin_project(p["requirement"], name=p["name"],
                                          target_dir=p["target_dir"])
            else:
                await q.edit_message_text("Ekhon start korar kichu nei 🙂"
                                          if not self._pending_project else "⏳ Already busy.")
        else:
            self._pending_project = None
            await q.edit_message_text("❌ Cancel korlam.")

    # ---- background project launch ----
    async def _begin_project(self, requirement: str, name: str | None = None,
                             target_dir: str | None = None) -> None:
        self._busy = True
        if target_dir is None:
            target_dir = self._pending_workdir
            self._pending_workdir = None

        async def _run() -> None:
            try:
                await self.orchestrator.handle_requirement(
                    requirement, name=name, target_dir=target_dir)
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
        router = orchestrator.glue.router if orchestrator.glue else None
        self.conversation = ConversationEngine(router)
        app = (Application.builder()
               .token(self.settings.telegram_bot_token)
               .post_init(self._post_init)
               .build())
        h = app.add_handler
        h(CommandHandler("start", self.cmd_start))
        h(CommandHandler("help", self.cmd_help))
        h(CommandHandler("new", self.cmd_new))
        h(CommandHandler("workdir", self.cmd_workdir))
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
        h(CallbackQueryHandler(self.on_project_callback, pattern=r"^pj:"))
        h(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app = app
        return app
