import datetime as dt
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from webtracker.locking import LockHeldError, RunLock
from webtracker.scheduler import next_run_times, run_dev_scheduler, systemd_on_calendar

TZ = ZoneInfo("Asia/Jerusalem")
ROOT = Path(__file__).resolve().parent.parent


def local(*args):
    return dt.datetime(*args, tzinfo=TZ)


def test_next_runs_same_day():
    runs = next_run_times(local(2026, 3, 10, 7, 59), 3)
    assert [r.strftime("%m-%d %H:%M") for r in runs] == ["03-10 08:00", "03-10 16:00", "03-11 08:00"]


def test_exact_time_is_not_repeated():
    assert next_run_times(local(2026, 3, 10, 8, 0), 1)[0] == local(2026, 3, 10, 16, 0)


def test_utc_input_is_converted():
    # 05:30 UTC in winter = 07:30 Israel time
    runs = next_run_times(dt.datetime(2026, 1, 5, 5, 30, tzinfo=dt.timezone.utc), 1)
    assert runs[0] == local(2026, 1, 5, 8, 0)
    assert runs[0].utcoffset() == dt.timedelta(hours=2)


def test_dst_offsets():
    winter = next_run_times(local(2026, 1, 15, 0, 0), 1)[0]
    summer = next_run_times(local(2026, 7, 15, 0, 0), 1)[0]
    assert winter.utcoffset() == dt.timedelta(hours=2)
    assert summer.utcoffset() == dt.timedelta(hours=3)
    assert winter.astimezone(dt.timezone.utc).hour == 6
    assert summer.astimezone(dt.timezone.utc).hour == 5


def test_naive_rejected():
    with pytest.raises(ValueError):
        next_run_times(dt.datetime(2026, 1, 1), 1)


def test_systemd_timer_matches_schedule():
    timer = (ROOT / "deploy/systemd/webtracker-research.timer").read_text()
    for line in systemd_on_calendar():
        assert line in timer
    assert "Persistent=true" in timer


def test_systemd_units_are_hardened():
    for name in ("webtracker-research.service", "webtracker-bot.service"):
        unit = (ROOT / "deploy/systemd" / name).read_text()
        for directive in ("NoNewPrivileges=yes", "ProtectSystem=strict", "User=webtracker", "LoadCredential="):
            assert directive in unit
        assert "dangerously" not in unit and "danger-full-access" not in unit
    assert "SuccessExitStatus=75" in (ROOT / "deploy/systemd/webtracker-research.service").read_text()


def test_dev_scheduler_waits_and_runs():
    clock = {"now": local(2026, 3, 10, 7, 58)}
    calls, sleeps = [], []

    def sleep(sec):
        sleeps.append(sec)
        clock["now"] += dt.timedelta(seconds=sec)

    run_dev_scheduler(lambda: calls.append(clock["now"]), now_fn=lambda: clock["now"], sleep_fn=sleep,
                      max_iterations=2)
    assert [c.strftime("%H:%M") for c in calls] == ["08:00", "16:00"]
    assert max(sleeps) <= 60


def test_dev_scheduler_survives_job_errors():
    clock = {"now": local(2026, 3, 10, 7, 59)}

    def sleep(sec):
        clock["now"] += dt.timedelta(seconds=sec)

    def job():
        raise RuntimeError("boom")

    run_dev_scheduler(job, now_fn=lambda: clock["now"], sleep_fn=sleep, max_iterations=2)


def test_lock_exclusive(tmp_path):
    a, b = RunLock(tmp_path / "x.lock"), RunLock(tmp_path / "x.lock")
    with a:
        assert b.is_held_elsewhere()
        with pytest.raises(LockHeldError):
            b.acquire()
    assert not b.is_held_elsewhere()
    with b:
        pass
    assert (tmp_path / "x.lock").stat().st_mode & 0o077 == 0
