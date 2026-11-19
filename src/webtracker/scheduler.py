"""Schedule definition and a development scheduler loop.

Production uses the systemd timer in ``deploy/systemd``; this module provides
the same schedule for development (``webtracker dev-scheduler``), for showing
the next run in ``/status``, and for rendering the timer's ``OnCalendar`` lines.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

log = logging.getLogger(__name__)

TIMEZONE = "Asia/Jerusalem"
RUN_TIMES: Tuple[dt.time, ...] = (dt.time(8, 0), dt.time(16, 0))


def next_run_times(
    now: dt.datetime,
    count: int = 2,
    *,
    tz: str = TIMEZONE,
    times: Sequence[dt.time] = RUN_TIMES,
) -> List[dt.datetime]:
    """Next ``count`` scheduled times strictly after ``now`` (aware datetimes in ``tz``)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    out: List[dt.datetime] = []
    day = local_now.date()
    while len(out) < count:
        for t in sorted(times):
            candidate = dt.datetime.combine(day, t, tzinfo=zone)
            if candidate > local_now:
                out.append(candidate)
                if len(out) == count:
                    break
        day += dt.timedelta(days=1)
    return out


def systemd_on_calendar(tz: str = TIMEZONE, times: Sequence[dt.time] = RUN_TIMES) -> List[str]:
    return [f"OnCalendar=*-*-* {t.strftime('%H:%M:%S')} {tz}" for t in sorted(times)]


def run_dev_scheduler(
    job: Callable[[], object],
    *,
    now_fn: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
    max_iterations: Optional[int] = None,
) -> None:
    """Sleep until each scheduled time and call ``job``. Overlap is prevented by the
    runner's file lock, and a slow job simply delays the next check."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        target = next_run_times(now_fn(), 1)[0]
        log.info("next scheduled run at %s", target.isoformat())
        while True:
            remaining = (target - now_fn()).total_seconds()
            if remaining <= 0:
                break
            sleep_fn(min(remaining, 60.0))
        try:
            job()
        except Exception:  # keep the loop alive
            log.exception("scheduled job failed")
        iterations += 1
