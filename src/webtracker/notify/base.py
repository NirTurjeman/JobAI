"""Notifier interface."""

from __future__ import annotations

import abc
from typing import List, Sequence

from ..db.models import Record


class NotificationError(Exception):
    pass


class Notifier(abc.ABC):
    @abc.abstractmethod
    def send_records(self, records: Sequence[Record], statuses: Sequence[str]) -> List[int]:
        """Send one notification per record. Returns IDs of records sent successfully."""


class NullNotifier(Notifier):
    """Used when notifications are disabled; sends nothing."""

    def send_records(self, records, statuses):
        return []


class MemoryNotifier(Notifier):
    """Collects notifications in memory (tests and dry runs)."""

    def __init__(self, fail_ids: Sequence[int] = ()) -> None:
        self.sent: List[Record] = []
        self.fail_ids = set(fail_ids)

    def send_records(self, records, statuses):
        ok = []
        for r in records:
            if r.id in self.fail_ids:
                continue
            self.sent.append(r)
            ok.append(r.id)
        return ok
