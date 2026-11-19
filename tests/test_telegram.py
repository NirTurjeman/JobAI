import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from webtracker.agents.mock import MockAgent
from webtracker.bot.app import BotHandlers, build_application, is_authorized
from webtracker.db import repository as repo
from webtracker.db.models import Record, StatusEvent, TrackedAction, UserFeedback
from webtracker.db.session import session_scope
from webtracker.dedup import CandidateRecord
from webtracker.notify.base import MemoryNotifier
from webtracker.notify.formatting import encode_callback, format_record, parse_callback
from webtracker.runner import ResearchRunner
from webtracker.schemas import AgentRecord
from webtracker.scoring import evaluate

from conftest import MOCK_FIXTURE, make_record

ALLOWED = 111
_NO_CALLBACK = object()


def update(user_id=ALLOWED, chat_type="private", callback_data=_NO_CALLBACK):
    message = SimpleNamespace(reply_text=AsyncMock())
    cq = None
    if callback_data is not _NO_CALLBACK:
        cq = SimpleNamespace(
            data=callback_data, answer=AsyncMock(), message=message, edit_message_reply_markup=AsyncMock()
        )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
        effective_chat=SimpleNamespace(id=user_id or 0, type=chat_type),
        effective_message=message,
        callback_query=cq,
    )


def ctx(args=()):
    return SimpleNamespace(args=list(args), bot=SimpleNamespace(send_message=AsyncMock()))


def seed(services, **kw):
    with session_scope(services.session_factory) as s:
        rec = AgentRecord.model_validate(make_record(**kw))
        return repo.upsert_record(s, CandidateRecord.from_agent_record(rec), evaluate(rec, services.cfg), None).record.id


# ------------------------------------------------------------------ authorization


def test_is_authorized():
    assert is_authorized(update(), ALLOWED)
    assert not is_authorized(update(user_id=222), ALLOWED)
    assert not is_authorized(update(chat_type="group"), ALLOWED)
    assert not is_authorized(update(user_id=None), ALLOWED)
    assert not is_authorized(update(), None)  # unconfigured → deny all


@pytest.mark.parametrize("cmd", ["start", "help", "records", "status", "scan", "fallback"])
async def test_unauthorized_commands_leak_nothing(services, cmd):
    seed(services, title="Secret-ish record")
    h = BotHandlers(services, runner_factory=lambda: pytest.fail("runner must not be created"))
    u = update(user_id=999)
    await getattr(h, cmd)(u, ctx())
    u.effective_message.reply_text.assert_awaited_once()
    assert u.effective_message.reply_text.await_args.args[0] == "Not authorized."
    assert h.scan_task is None


async def test_unauthorized_callback_changes_nothing(services):
    rid = seed(services)
    h = BotHandlers(services)
    u = update(user_id=999, callback_data=encode_callback("d", rid))
    await h.on_callback(u, ctx())
    u.callback_query.answer.assert_awaited_once_with("Not authorized.")
    with session_scope(services.session_factory) as s:
        assert s.get(Record, rid).status == "new"
        assert s.scalars(select(TrackedAction)).all() == []


def test_build_application_requires_configuration(services):
    with pytest.raises(RuntimeError):
        build_application(services)  # no token configured


# ------------------------------------------------------------------ commands


async def test_records_and_status_commands(services):
    seed(services, title="<b>Item</b> & co")
    h = BotHandlers(services)
    u = update()
    await h.records(u, ctx())
    text = u.effective_message.reply_text.await_args.args[0]
    assert "&lt;b&gt;Item&lt;/b&gt; &amp; co" in text  # HTML escaped

    u = update()
    await h.records(u, ctx(["nonsense"]))
    assert "Unknown status" in u.effective_message.reply_text.await_args.args[0]

    u = update()
    await h.status(u, ctx())
    text = u.effective_message.reply_text.await_args.args[0]
    assert "new: 1" in text and "Next scheduled runs" in text


async def test_scan_runs_in_background_with_cooldown(services):
    created = []

    def factory():
        r = ResearchRunner(services.cfg, services.session_factory, MockAgent(MOCK_FIXTURE), MemoryNotifier(),
                           lock=services.lock)
        created.append(r)
        return r

    services.settings.scan_cooldown_seconds = 3600
    h = BotHandlers(services, runner_factory=factory)
    c = ctx()
    await h.scan(update(), c)
    summary = await h.scan_task
    assert summary.new == 3 and len(created) == 1
    c.bot.send_message.assert_awaited_once()
    assert "3 new" in c.bot.send_message.await_args.kwargs["text"]

    u = update()
    await h.scan(u, ctx())
    assert "wait" in u.effective_message.reply_text.await_args.args[0]
    assert len(created) == 1


async def test_scan_refused_while_lock_held(services):
    lock = services.lock
    lock.acquire()
    try:
        h = BotHandlers(services, runner_factory=lambda: pytest.fail("must not run"))
        u = update()
        await h.scan(u, ctx())
        assert "already in progress" in u.effective_message.reply_text.await_args.args[0]
    finally:
        lock.release()


# ------------------------------------------------------------------ buttons


async def test_callback_actions(services):
    rid = seed(services)
    h = BotHandlers(services)

    await h.on_callback(update(callback_data=encode_callback("v", rid)), ctx())
    await h.on_callback(update(callback_data=encode_callback("s", rid)), ctx())
    u = update(callback_data=encode_callback("u", rid))
    await h.on_callback(u, ctx())
    u.callback_query.edit_message_reply_markup.assert_awaited_once()
    idx = services.cfg.statuses.index("in_progress")
    await h.on_callback(update(callback_data=encode_callback("t", rid, idx)), ctx())
    await h.on_callback(update(callback_data=encode_callback("d", rid)), ctx())

    with session_scope(services.session_factory) as s:
        rec = s.get(Record, rid)
        assert rec.status == "dismissed"
        history = [e.new_status for e in s.scalars(select(StatusEvent).where(StatusEvent.record_id == rid).order_by(StatusEvent.id))]
        assert history == ["new", "viewed", "saved", "in_progress", "dismissed"]
        actions = [a.action for a in s.scalars(select(TrackedAction).order_by(TrackedAction.id))]
        assert actions == ["view", "save", "status_change", "dismiss"]
        fb = [f.feedback for f in s.scalars(select(UserFeedback).order_by(UserFeedback.id))]
        assert fb == ["relevant", "not_relevant"]


@pytest.mark.parametrize("data", ["x:1", "v:abc", "v:1;rm -rf", "t:1", "v:1:2", "", None, "t:1:99"])
async def test_bad_callback_data(services, data):
    seed(services)
    h = BotHandlers(services)
    u = update(callback_data=data)
    await h.on_callback(u, ctx())
    u.callback_query.answer.assert_awaited_once()
    with session_scope(services.session_factory) as s:
        assert s.scalars(select(StatusEvent)).all()[-1].new_status == "new"


async def test_callback_unknown_record(services):
    u = update(callback_data=encode_callback("v", 12345))
    await BotHandlers(services).on_callback(u, ctx())
    u.callback_query.answer.assert_awaited_once_with("Record not found.")


# ------------------------------------------------------------------ formatting


def test_callback_roundtrip():
    assert parse_callback(encode_callback("t", 5, 2)).action == "set_status"
    assert parse_callback("v:5").record_id == 5
    with pytest.raises(ValueError):
        encode_callback("z", 1)


def test_format_record_escapes_and_contains_fields():
    r = Record(id=7, title="<script>x</script>", source_name="Src", url="https://example.com/a?b=1&c=2",
               description="desc <i>", relevance_score=0.875, explanation="because <b>", status="new",
               metadata_json={"k": "<v>"})
    text = format_record(r, detailed=True)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "0.88" in text and "because &lt;b&gt;" in text
    assert 'href="https://example.com/a?b=1&amp;c=2"' in text
    assert "&lt;v&gt;" in text


def test_format_record_unsafe_url_not_linked():
    r = Record(id=1, title="t", source_name="s", url="javascript:alert(1)", description="",
               relevance_score=0.1, explanation="", status="new", metadata_json={})
    assert "href" not in format_record(r)
