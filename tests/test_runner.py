import json

from sqlalchemy import select

from webtracker.agents.mock import MockAgent
from webtracker.db.models import AgentError, Record, RecordScore, ResearchRun, TrackedAction
from webtracker.db.session import session_scope
from webtracker.locking import RunLock
from webtracker.notify.base import MemoryNotifier
from webtracker.runner import ResearchRunner

from conftest import MOCK_FIXTURE, make_record


def runner(cfg, sf, agent, notifier=None, lock=None, **kw):
    return ResearchRunner(cfg, sf, agent, notifier or MemoryNotifier(), lock=lock, **kw)


def test_mock_end_to_end(cfg, session_factory, tmp_path):
    notifier = MemoryNotifier()
    r = runner(cfg, session_factory, MockAgent(MOCK_FIXTURE), notifier, RunLock(tmp_path / "l"))
    summary = r.run("manual")
    assert summary.status == "partial"  # one invalid (localhost) record in the fixture
    assert (summary.returned, summary.new, summary.updated, summary.invalid) == (5, 3, 0, 1)
    # first (0.93) and second (0.755) qualify; archived one is excluded by criteria
    assert [rec.title for rec in notifier.sent] == [
        "Example Topic: First Sample Announcement",
        "Second Sample Listing (remote)",
    ]
    assert summary.notified == 2

    with session_scope(session_factory) as s:
        run = s.get(ResearchRun, summary.run_id)
        assert run.status == "partial" and run.finished_at is not None
        assert run.records_new == 3 and run.notifications_sent == 2
        errors = s.scalars(select(AgentError)).all()
        assert [e.stage for e in errors] == ["validation"]
        assert len(s.scalars(select(RecordScore)).all()) == 3
        notified = s.scalars(select(Record).where(Record.notified_at.is_not(None))).all()
        assert len(notified) == 2
        assert len(s.scalars(select(TrackedAction).where(TrackedAction.action == "notify")).all()) == 2

    # Second run: no duplicates, no repeated notifications.
    notifier2 = MemoryNotifier()
    s2 = runner(cfg, session_factory, MockAgent(MOCK_FIXTURE), notifier2).run("timer")
    assert (s2.new, s2.updated, s2.notified) == (0, 3, 0)
    assert notifier2.sent == []
    with session_scope(session_factory) as s:
        assert len(s.scalars(select(Record)).all()) == 3


def test_prompt_contains_known_records_on_second_run(cfg, session_factory):
    runner(cfg, session_factory, MockAgent(MOCK_FIXTURE)).run()
    agent = MockAgent(MOCK_FIXTURE)
    runner(cfg, session_factory, agent).run()
    prompt = agent.calls[0].prompt
    assert "https://example.org/listings/42" in prompt
    assert agent.calls[0].output_schema["additionalProperties"] is False


def test_agent_failure_recorded(cfg, session_factory):
    s = runner(cfg, session_factory, MockAgent(fail_with="boom")).run()
    assert s.status == "failed"
    with session_scope(session_factory) as ss:
        run = ss.get(ResearchRun, s.run_id)
        assert run.status == "failed" and "boom" in run.error_summary
        assert ss.scalars(select(AgentError)).one().stage == "agent"


def test_unexpected_agent_exception_does_not_crash(cfg, session_factory):
    class Broken(MockAgent):
        def research(self, task):
            raise KeyError("x")

    assert runner(cfg, session_factory, Broken()).run().status == "failed"


def test_non_json_output(cfg, session_factory):
    s = runner(cfg, session_factory, MockAgent(raw_output="Sure! Here are results: none")).run()
    assert s.status == "failed"
    with session_scope(session_factory) as ss:
        assert ss.scalars(select(AgentError)).one().stage == "parse"


def test_envelope_with_extra_keys_rejected(cfg, session_factory):
    payload = {"records": [], "sources_consulted": [], "notes": None, "shell": "rm -rf /"}
    assert runner(cfg, session_factory, MockAgent(payload=payload)).run().status == "failed"


def test_blocked_domain_record_rejected(cfg, session_factory):
    payload = {"records": [make_record(url="https://news.example.net/x")], "sources_consulted": [], "notes": None}
    s = runner(cfg, session_factory, MockAgent(payload=payload)).run()
    assert (s.new, s.invalid) == (0, 1)


def test_notification_failure_retried_next_run(cfg, session_factory):
    payload = {"records": [make_record(title="Example topic item", relevance_score=0.95)],
               "sources_consulted": [], "notes": None}
    failing = MemoryNotifier(fail_ids=[1])
    s1 = runner(cfg, session_factory, MockAgent(payload=payload), failing).run()
    assert s1.status == "partial" and s1.notified == 0
    ok = MemoryNotifier()
    s2 = runner(cfg, session_factory, MockAgent(payload={"records": [], "sources_consulted": [], "notes": None}), ok).run()
    assert s2.notified == 1 and ok.sent[0].id == 1


def test_notifier_exception_handled(cfg, session_factory):
    class Exploding(MemoryNotifier):
        def send_records(self, records, statuses):
            raise RuntimeError("network down")

    payload = {"records": [make_record(title="Example topic item", relevance_score=0.95)],
               "sources_consulted": [], "notes": None}
    s = runner(cfg, session_factory, MockAgent(payload=payload), Exploding()).run()
    assert s.status == "partial" and s.new == 1


def test_no_notify_flag(cfg, session_factory):
    n = MemoryNotifier()
    s = runner(cfg, session_factory, MockAgent(MOCK_FIXTURE), n, notify=False).run()
    assert s.new == 3 and n.sent == []


def test_store_min_score_and_max_records(cfg, session_factory):
    cfg = cfg.model_copy(deep=True)
    cfg.scoring.store_min_score = 0.5
    cfg.limits.max_records_per_run = 2
    payload = {
        "records": [make_record(url=f"https://example.com/{i}", title=f"T{i}", relevance_score=s)
                    for i, s in enumerate([0.1, 0.9, 0.9])],
        "sources_consulted": [], "notes": None,
    }
    s = runner(cfg, session_factory, MockAgent(payload=payload)).run()
    assert s.below_threshold == 1 and s.new == 1


def test_overlapping_runs_prevented(cfg, session_factory, tmp_path):
    lock_path = tmp_path / "run.lock"
    holder = RunLock(lock_path)
    holder.acquire()
    try:
        agent = MockAgent(MOCK_FIXTURE)
        s = runner(cfg, session_factory, agent, lock=RunLock(lock_path)).run()
        assert s.status == "skipped" and agent.calls == []
    finally:
        holder.release()
    assert runner(cfg, session_factory, MockAgent(MOCK_FIXTURE), lock=RunLock(lock_path)).run().status == "partial"


def test_errors_are_redacted(cfg, session_factory):
    secret = "123456789:" + "S" * 35
    agent = MockAgent(fail_with=f"failed with token {secret}")
    runner(cfg, session_factory, agent, redact=lambda t: t.replace(secret, "[REDACTED]")).run()
    with session_scope(session_factory) as ss:
        err = ss.scalars(select(AgentError)).one()
        run = ss.scalars(select(ResearchRun)).one()
        assert secret not in err.message and secret not in run.error_summary
