"""Logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Iterable, List

# Patterns for common secret formats, redacted even if not explicitly registered.
_PATTERNS = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),            # Telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                   # OpenAI-style API key
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),      # Authorization headers
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)=([^\s&]+)"),
]

REDACTED = "[REDACTED]"


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets: List[str] = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, REDACTED)
        for pat in _PATTERNS:
            if pat.groups == 2:
                text = pat.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
            elif pat.groups == 1:
                text = pat.sub(lambda m: m.group(1) + REDACTED, text)
            else:
                text = pat.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = self.redact(msg)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> RedactingFilter:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    flt = RedactingFilter(secrets)
    handler.addFilter(flt)
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return flt
