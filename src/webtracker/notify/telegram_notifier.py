"""Send record notifications to the single configured Telegram chat."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Sequence

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from ..db.models import Record
from .base import NotificationError, Notifier
from .formatting import format_record, record_keyboard

log = logging.getLogger(__name__)


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: int, *, delay_seconds: float = 1.0) -> None:
        if not token or not chat_id:
            raise NotificationError("Telegram token and chat id are required")
        self._token = token
        self.chat_id = chat_id
        self.delay_seconds = delay_seconds

    def send_records(self, records: Sequence[Record], statuses: Sequence[str]) -> List[int]:
        if not records:
            return []
        return asyncio.run(self._send_all(list(records)))

    async def _send_all(self, records: List[Record]) -> List[int]:
        sent: List[int] = []
        async with Bot(self._token) as bot:
            for rec in records:
                for attempt in range(3):
                    try:
                        await bot.send_message(
                            chat_id=self.chat_id,
                            text=format_record(rec),
                            parse_mode=ParseMode.HTML,
                            reply_markup=record_keyboard(rec),
                            disable_web_page_preview=True,
                        )
                        sent.append(rec.id)
                        break
                    except RetryAfter as exc:
                        wait = getattr(exc, "retry_after", 5)
                        wait = wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)
                        await asyncio.sleep(min(wait, 60))
                    except TelegramError as exc:
                        log.warning("telegram send failed for record %s: %s", rec.id, type(exc).__name__)
                        break
                await asyncio.sleep(self.delay_seconds)  # stay well under Telegram rate limits
        return sent
