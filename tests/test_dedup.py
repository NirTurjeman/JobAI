from webtracker.db import repository as repo
from webtracker.db.models import RecordSource
from webtracker.db.session import session_scope
from webtracker.dedup import content_fingerprint, dedupe_batch, url_fingerprint
from webtracker.schemas import AgentRecord
from webtracker.scoring import evaluate

from conftest import make_record


def recs(*dicts):
    return [AgentRecord.model_validate(make_record(**d)) for d in dicts]


def test_fingerprints_are_deterministic():
    assert url_fingerprint("https://www.example.com/a/?utm_medium=x") == url_fingerprint("http://example.com/a")
    assert url_fingerprint("https://example.com/a") != url_fingerprint("https://example.com/b")
    assert content_fingerprint("Hello, World!", "https://example.com/1") == content_fingerprint(
        "hello world", "https://www.example.com/2"
    )
    assert content_fingerprint("Hello", "https://a.com/") != content_fingerprint("Hello", "https://b.com/")


def test_batch_dedup_by_url_and_content():
    out = dedupe_batch(
        recs(
            {"url": "https://example.com/item/1", "relevance_score": 0.5},
            {"url": "https://www.example.com/item/1/?utm_source=z", "relevance_score": 0.9},
            {"url": "https://example.com/other", "title": "Sample   item!"},  # same title + domain
            {"url": "https://example.com/item/2", "title": "Different"},
        )
    )
    assert len(out) == 2
    first = out[0]
    assert first.record.relevance_score == 0.9
    assert url_fingerprint("https://example.com/other") in first.alt_urls


def test_batch_dedup_via_additional_urls():
    out = dedupe_batch(
        recs(
            {"url": "https://a.example.com/x", "title": "A", "additional_urls": ["https://b.example.org/y"]},
            {"url": "https://b.example.org/y", "title": "B"},
        )
    )
    assert len(out) == 1


def test_batch_dedup_order_independent_count():
    data = [
        {"url": "https://example.com/1", "title": "One"},
        {"url": "https://example.com/1?utm_campaign=c", "title": "One again"},
        {"url": "https://example.com/2", "title": "Two"},
    ]
    assert len(dedupe_batch(recs(*data))) == len(dedupe_batch(recs(*reversed(data)))) == 2


def _store(s, cfg, rec):
    cand = dedupe_batch([rec])[0]
    return repo.upsert_record(s, cand, evaluate(rec, cfg), None)


def test_db_dedup_updates_last_seen_and_sources(session_factory, cfg):
    with session_scope(session_factory) as s:
        first = _store(s, cfg, recs({"url": "https://example.com/item/1"})[0])
        first_seen = first.record.last_seen_at
        rid = first.record.id
    with session_scope(session_factory) as s:
        again = _store(s, cfg, recs({"url": "https://www.example.com/item/1#x", "description": "updated"})[0])
        assert not again.created and again.record.id == rid
        assert again.record.last_seen_at >= first_seen
        assert again.record.description == "updated"
        # same title on same domain via another URL → same record, new source row
        third = _store(s, cfg, recs({"url": "https://example.com/mirror/9"})[0])
        assert not third.created and third.record.id == rid
        sources = s.query(RecordSource).filter_by(record_id=rid).all()
        assert len(sources) == 2


def test_db_dedup_keeps_user_status(session_factory, cfg):
    with session_scope(session_factory) as s:
        r = _store(s, cfg, recs({})[0]).record
        repo.set_status(s, r, "dismissed", allowed=cfg.statuses, actor="t")
    with session_scope(session_factory) as s:
        r2 = _store(s, cfg, recs({})[0]).record
        assert r2.status == "dismissed"
