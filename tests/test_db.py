import datetime as dt

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from webtracker.db import repository as repo
from webtracker.db.models import Base, Record, StatusEvent
from webtracker.db.session import session_scope, upgrade_database
from webtracker.dedup import CandidateRecord
from webtracker.schemas import AgentRecord
from webtracker.scoring import evaluate

from conftest import make_record

TABLES = {
    "records", "record_sources", "record_scores", "tracked_actions",
    "status_events", "research_runs", "agent_errors", "user_feedback",
}


def test_migrations_create_all_tables(db_url):
    engine = create_engine(db_url)
    names = set(inspect(engine).get_table_names())
    assert TABLES <= names
    assert "alembic_version" in names


def test_migrations_match_models(db_url):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_upgrade_is_idempotent_and_downgrade_works(tmp_path):
    from alembic import command
    from webtracker.db.session import alembic_config

    url = f"sqlite:///{tmp_path / 'x.sqlite3'}"
    upgrade_database(url)
    upgrade_database(url)
    command.downgrade(alembic_config(url), "base")
    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}


def test_db_file_permissions(tmp_path):
    url = f"sqlite:///{tmp_path / 'perm.sqlite3'}"
    upgrade_database(url)
    assert (tmp_path / "perm.sqlite3").stat().st_mode & 0o077 == 0


def _store(s, cfg, run_id=None, **kw):
    cand = CandidateRecord.from_agent_record(AgentRecord.model_validate(make_record(**kw)))
    return repo.upsert_record(s, cand, evaluate(cand.record, cfg), run_id)


def test_record_crud_and_status_history(session_factory, cfg):
    with session_scope(session_factory) as s:
        run = repo.start_run(s, trigger="manual", agent_backend="mock", config_name="t", config_hash="h")
        res = _store(s, cfg, run.id)
        assert res.created
        rec = res.record
        assert rec.status == "new"
        assert rec.discovered_at.tzinfo is not None
        assert repo.set_status(s, rec, "saved", allowed=cfg.statuses, actor="test")
        assert not repo.set_status(s, rec, "saved", allowed=cfg.statuses, actor="test")
        with pytest.raises(repo.InvalidStatusError):
            repo.set_status(s, rec, "bogus", allowed=cfg.statuses, actor="test")
        rid = rec.id
    with session_scope(session_factory) as s:
        events = s.scalars(select(StatusEvent).where(StatusEvent.record_id == rid).order_by(StatusEvent.id)).all()
        assert [(e.old_status, e.new_status) for e in events] == [(None, "new"), ("new", "saved")]
        assert repo.status_counts(s) == [("saved", 1)]


def test_unique_url_fingerprint_enforced(session_factory):
    now = dt.datetime.now(dt.timezone.utc)
    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as s:
            for _ in range(2):
                s.add(Record(title="t", source_name="s", url="https://e.com", url_fingerprint="same",
                             content_fingerprint="c", description="", metadata_json={}, relevance_score=0,
                             explanation="", status="new", discovered_at=now, last_seen_at=now))
            s.flush()


def test_naive_datetimes_rejected(session_factory):
    with pytest.raises(Exception):
        with session_scope(session_factory) as s:
            s.add(Record(title="t", source_name="s", url="https://e.com", url_fingerprint="x",
                         content_fingerprint="c", discovered_at=dt.datetime.now(), last_seen_at=dt.datetime.now()))
            s.flush()


def test_cascade_delete(session_factory, cfg):
    with session_scope(session_factory) as s:
        rec = _store(s, cfg).record
        repo.track_action(s, rec.id, "view", "test")
        repo.add_feedback(s, rec.id, "test", "relevant")
        rid = rec.id
    with session_scope(session_factory) as s:
        s.delete(s.get(Record, rid))
    with session_scope(session_factory) as s:
        assert s.scalars(select(StatusEvent)).all() == []
