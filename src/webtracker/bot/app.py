"""Telegram bot: commands, inline buttons and authorization.

Only the configured Telegram user ID, in a private chat, may use the bot. The
bot exposes a fixed set of operations on the database and an in-process call
to the research runner; no message content is ever executed or passed to a
shell.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
from typing import Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ..db import repository as repo
from ..db.session import session_scope
from ..notify.formatting import encode_callback, format_record, parse_callback, record_keyboard, status_keyboard
from ..runner import ResearchRunner, RunSummary
from ..scheduler import next_run_times
from ..services import Services, make_runner

log = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Commands</b>\n"
    "/records [status] — latest records (optionally filtered by status)\n"
    "/status — last run, record counts and next scheduled runs\n"
    "/scan — start a research run now\n"
    "/help — this message\n\n"
    "Use the buttons under each record to view, save, dismiss or change its status."
)


def is_authorized(update: Update, allowed_user_id: Optional[int]) -> bool:
    if allowed_user_id is None:
        return False
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False
    return user.id == allowed_user_id and chat.type == ChatType.PRIVATE


class BotHandlers:
    def __init__(
        self,
        services: Services,
        runner_factory: Optional[Callable[[], ResearchRunner]] = None,
        now_fn: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    ) -> None:
        self.services = services
        self.allowed_user_id = services.settings.telegram_allowed_user_id
        self.runner_factory = runner_factory or (lambda: make_runner(services))
        self.now_fn = now_fn
        self.scan_task: Optional[asyncio.Task] = None
        self.last_scan_started: Optional[dt.datetime] = None

    # ------------------------------------------------------------- helpers

    async def _guard(self, update: Update) -> bool:
        if is_authorized(update, self.allowed_user_id):
            return True
        uid = update.effective_user.id if update.effective_user else None
        log.warning("unauthorized Telegram access attempt from user id %s", uid)
        if update.callback_query is not None:
            await update.callback_query.answer("Not authorized.")
        elif update.effective_message is not None:
            await update.effective_message.reply_text("Not authorized.")
        return False

    def _actor(self, update: Update) -> str:
        return f"telegram:{update.effective_user.id}"

    async def _reply(self, update: Update, text: str, markup=None) -> None:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True
        )

    # ------------------------------------------------------------- commands

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        name = html.escape(self.services.cfg.name)
        await self._reply(update, f"Research tracker <b>{name}</b> is running.\n\n" + HELP_TEXT)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await self._reply(update, HELP_TEXT)

    async def records(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        args = list(getattr(context, "args", None) or [])
        status = None
        if args:
            candidate = args[0].strip().lower()
            if candidate not in self.services.cfg.statuses:
                allowed = ", ".join(self.services.cfg.statuses)
                await self._reply(update, f"Unknown status. Use one of: {html.escape(allowed)}")
                return
            status = candidate
        with session_scope(self.services.session_factory) as s:
            recs = repo.list_records(s, status=status, limit=10, exclude_statuses=() if status else ("dismissed",))
            if not recs:
                await self._reply(update, "No records found.")
                return
            lines, buttons = [], []
            for r in recs:
                lines.append(
                    f"#{r.id} · {r.relevance_score:.2f} · {html.escape(r.status)}\n"
                    f"<b>{html.escape(r.title[:150])}</b> — {html.escape(r.source_name[:60])}"
                )
                buttons.append([InlineKeyboardButton(f"View #{r.id}", callback_data=encode_callback("v", r.id))])
        title = f"Latest records ({html.escape(status)})" if status else "Latest records"
        await self._reply(update, f"<b>{title}</b>\n\n" + "\n\n".join(lines), InlineKeyboardMarkup(buttons))

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        with session_scope(self.services.session_factory) as s:
            run = repo.last_run(s)
            counts = repo.status_counts(s)
            run_text = "No runs yet."
            if run:
                when = run.started_at.strftime("%Y-%m-%d %H:%M UTC")
                run_text = (
                    f"Last run #{run.id} ({html.escape(run.trigger)}, {when}): <b>{html.escape(run.status)}</b>\n"
                    f"{run.records_returned} returned, {run.records_new} new, {run.records_updated} updated, "
                    f"{run.records_invalid} invalid, {run.notifications_sent} notified"
                )
        running = (self.scan_task is not None and not self.scan_task.done()) or self.services.lock.is_held_elsewhere()
        counts_text = ", ".join(f"{html.escape(k)}: {v}" for k, v in counts) or "none"
        nxt = ", ".join(t.strftime("%a %H:%M %Z") for t in next_run_times(self.now_fn(), 2))
        await self._reply(
            update,
            f"{run_text}\n\nRecords — {counts_text}\nRun in progress: {'yes' if running else 'no'}\n"
            f"Next scheduled runs: {nxt}",
        )

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if (self.scan_task is not None and not self.scan_task.done()) or self.services.lock.is_held_elsewhere():
            await self._reply(update, "A research run is already in progress.")
            return
        now = self.now_fn()
        cooldown = dt.timedelta(seconds=self.services.settings.scan_cooldown_seconds)
        if self.last_scan_started and now - self.last_scan_started < cooldown:
            wait = int((cooldown - (now - self.last_scan_started)).total_seconds() // 60) + 1
            await self._reply(update, f"Please wait about {wait} min before another manual scan.")
            return
        self.last_scan_started = now
        await self._reply(update, "Research run started. You will get a summary when it finishes.")
        chat_id = update.effective_chat.id
        bot = context.bot
        self.scan_task = asyncio.ensure_future(self._run_scan(bot, chat_id))

    async def _run_scan(self, bot, chat_id: int) -> RunSummary:
        try:
            summary = await asyncio.to_thread(lambda: self.runner_factory().run("telegram"))
            text = summary.as_text()
        except Exception as exc:
            log.exception("manual scan failed")
            summary = RunSummary(status="failed", message=type(exc).__name__)
            text = f"Research run failed ({type(exc).__name__})."
        await bot.send_message(chat_id=chat_id, text=html.escape(text), parse_mode=ParseMode.HTML)
        return summary

    async def fallback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Any other message: never interpreted, only answered with help."""
        if not await self._guard(update):
            return
        await self._reply(update, "Unknown command.\n\n" + HELP_TEXT)

    # ------------------------------------------------------------- buttons

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        query = update.callback_query
        cb = parse_callback(query.data)
        if cb is None:
            await query.answer("Unsupported action.")
            return
        statuses = self.services.cfg.statuses
        actor = self._actor(update)
        with session_scope(self.services.session_factory) as s:
            rec = repo.get_record(s, cb.record_id)
            if rec is None:
                await query.answer("Record not found.")
                return
            if cb.action == "view":
                repo.track_action(s, rec.id, "view", actor)
                if rec.status == "new" and "viewed" in statuses:
                    repo.set_status(s, rec, "viewed", allowed=statuses, actor=actor, reason="viewed in Telegram")
                await query.answer()
                await query.message.reply_text(
                    format_record(rec, detailed=True), parse_mode=ParseMode.HTML,
                    reply_markup=record_keyboard(rec), disable_web_page_preview=True,
                )
                return
            if cb.action == "save":
                target = "saved" if "saved" in statuses else None
                repo.track_action(s, rec.id, "save", actor)
                repo.add_feedback(s, rec.id, actor, "relevant")
                if target:
                    repo.set_status(s, rec, target, allowed=statuses, actor=actor, reason="saved in Telegram")
                await query.answer("Saved.")
                return
            if cb.action == "dismiss":
                repo.track_action(s, rec.id, "dismiss", actor)
                repo.add_feedback(s, rec.id, actor, "not_relevant")
                repo.set_status(s, rec, "dismissed", allowed=statuses, actor=actor, reason="dismissed in Telegram")
                await query.answer("Dismissed.")
                return
            if cb.action == "status_menu":
                await query.answer()
                await query.edit_message_reply_markup(reply_markup=status_keyboard(rec, statuses))
                return
            if cb.action == "set_status":
                if cb.arg is None or cb.arg >= len(statuses):
                    await query.answer("Unknown status.")
                    return
                new_status = statuses[cb.arg]
                changed = repo.set_status(s, rec, new_status, allowed=statuses, actor=actor, reason="Telegram status menu")
                repo.track_action(s, rec.id, "status_change", actor, {"status": new_status, "changed": changed})
                await query.answer(f"Status: {new_status}")
                await query.edit_message_reply_markup(reply_markup=record_keyboard(rec))
                return
        await query.answer("Unsupported action.")


def build_application(services: Services, handlers: Optional[BotHandlers] = None) -> Application:
    settings = services.settings
    if not settings.telegram_bot_token:
        raise RuntimeError("WT_TELEGRAM_BOT_TOKEN is not configured")
    if settings.telegram_allowed_user_id is None:
        raise RuntimeError("WT_TELEGRAM_ALLOWED_USER_ID is not configured")
    h = handlers or BotHandlers(services)
    app = Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    app.add_handler(CommandHandler("start", h.start))
    app.add_handler(CommandHandler("help", h.help))
    app.add_handler(CommandHandler("records", h.records))
    app.add_handler(CommandHandler("status", h.status))
    app.add_handler(CommandHandler("scan", h.scan))
    app.add_handler(CallbackQueryHandler(h.on_callback))
    app.add_handler(MessageHandler(filters.ALL, h.fallback))
    return app


def run_bot(services: Services) -> None:
    app = build_application(services)
    log.info("Telegram bot starting (long polling)")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)
