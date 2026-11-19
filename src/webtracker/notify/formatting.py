"""Telegram message formatting and callback-data encoding.

Every piece of record text originates from the web, so it is HTML-escaped and
sent with ``parse_mode=HTML``; links are only rendered for validated http(s)
URLs. Callback data is a short, strictly parsed ``<action>:<record id>[:<n>]``.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..db.models import Record
from ..sanitize import UnsafeURLError, validate_public_url

MAX_MESSAGE = 4000  # Telegram's limit is 4096 characters

ACTIONS = {"v": "view", "s": "save", "d": "dismiss", "u": "status_menu", "t": "set_status"}
_CB_RE = re.compile(r"^([vsdut]):(\d{1,12})(?::(\d{1,2}))?$")


@dataclass(frozen=True)
class Callback:
    action: str
    record_id: int
    arg: Optional[int] = None


def encode_callback(action_code: str, record_id: int, arg: Optional[int] = None) -> str:
    data = f"{action_code}:{record_id}" + (f":{arg}" if arg is not None else "")
    if not _CB_RE.match(data):
        raise ValueError(f"invalid callback data: {data!r}")
    return data


def parse_callback(data: Optional[str]) -> Optional[Callback]:
    if not data:
        return None
    m = _CB_RE.match(data)
    if not m:
        return None
    code, rid, arg = m.groups()
    if (code == "t") != (arg is not None):
        return None
    return Callback(ACTIONS[code], int(rid), int(arg) if arg is not None else None)


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _link(url: str) -> str:
    try:
        safe = validate_public_url(url)
    except UnsafeURLError:
        return _esc(url)
    return f'<a href="{_esc(safe)}">{_esc(safe)}</a>'


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def format_record(record: Record, *, detailed: bool = False) -> str:
    parts = [
        f"<b>{_esc(_truncate(record.title, 300))}</b>",
        f"Source: {_esc(record.source_name)}",
        f"Score: {record.relevance_score:.2f} · Status: {_esc(record.status)} · ID {record.id}",
        "",
        _esc(_truncate(record.description or "", 1500 if detailed else 600)),
    ]
    if record.explanation:
        parts += ["", f"<i>Why:</i> {_esc(_truncate(record.explanation, 800 if detailed else 400))}"]
    if detailed and record.metadata_json:
        parts.append("")
        for k, v in list(record.metadata_json.items())[:20]:
            parts.append(f"• {_esc(str(k))}: {_esc(_truncate(str(v), 200))}")
    parts += ["", _link(record.url)]
    text = "\n".join(parts)
    return text if len(text) <= MAX_MESSAGE else _truncate(text, MAX_MESSAGE)


def record_keyboard(record: Record) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("View", callback_data=encode_callback("v", record.id)),
                InlineKeyboardButton("Save", callback_data=encode_callback("s", record.id)),
                InlineKeyboardButton("Dismiss", callback_data=encode_callback("d", record.id)),
            ],
            [InlineKeyboardButton("Update status", callback_data=encode_callback("u", record.id))],
        ]
    )


def status_keyboard(record: Record, statuses: Sequence[str]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(statuses[:60]):
        label = f"• {s}" if s == record.status else s
        row.append(InlineKeyboardButton(label, callback_data=encode_callback("t", record.id, i)))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)
