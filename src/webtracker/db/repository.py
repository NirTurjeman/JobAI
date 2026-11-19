"""Data-access functions. All writes go through here."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..dedup import CandidateRecord
from ..scoring import ScoreResult
from .models import (
    AgentError,
    Record,
    RecordScore,
    RecordSource,
    ResearchRun,
    StatusEvent,
    TrackedAction,
    UserFeedback,
    utcnow,
)


class InvalidStatusError(ValueError):
    pass


@dataclass
class UpsertResult:
    record: Record
    created: bool


# --------------------------------------------------------------------------- runs


def start_run(session: Session, *, trigger: str, agent_backend: str, config_name: str, config_hash: str) -> ResearchRun:
    run = ResearchRun(trigger=trigger, agent_backend=agent_backend, config_name=config_name, config_hash=config_hash)
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: ResearchRun, status: str, error_summary: Optional[str] = None) -> None:
    run.status = status
    run.finished_at = utcnow()
    run.error_summary = error_summary[:2000] if error_summary else None
    session.flush()


def log_error(
    session: Session,
    run_id: Optional[int],
    stage: str,
    error_type: str,
    message: str,
    raw_excerpt: Optional[str] = None,
) -> AgentError:
    err = AgentError(
        run_id=run_id,
        stage=stage,
        error_type=error_type[:100],
        message=message[:4000],
        raw_excerpt=raw_excerpt[:2000] if raw_excerpt else None,
    )
    session.add(err)
    session.flush()
    return err


def last_run(session: Session) -> Optional[ResearchRun]:
    return session.scalars(select(ResearchRun).order_by(ResearchRun.id.desc()).limit(1)).first()


def last_run_by_trigger(session: Session, trigger: str) -> Optional[ResearchRun]:
    return session.scalars(
        select(ResearchRun).where(ResearchRun.trigger == trigger).order_by(ResearchRun.id.desc()).limit(1)
    ).first()


# --------------------------------------------------------------------------- records


def find_existing(session: Session, cand: CandidateRecord) -> Optional[Record]:
    fps = cand.all_url_fingerprints()
    rec = session.scalars(select(Record).where(Record.url_fingerprint.in_(fps))).first()
    if rec:
        return rec
    rec = session.scalars(
        select(Record).join(RecordSource).where(RecordSource.url_fingerprint.in_(fps))
    ).first()
    if rec:
        return rec
    return session.scalars(
        select(Record).where(Record.content_fingerprint == cand.content_fp).order_by(Record.id).limit(1)
    ).first()


def _add_source(session: Session, record: Record, fp: str, url: str, source_name: str, run_id: Optional[int]) -> None:
    now = utcnow()
    src = session.scalars(
        select(RecordSource).where(RecordSource.record_id == record.id, RecordSource.url_fingerprint == fp)
    ).first()
    if src:
        src.last_seen_at = now
        if run_id is not None:
            src.run_id = run_id
    else:
        session.add(
            RecordSource(
                record_id=record.id, source_name=source_name, url=url, url_fingerprint=fp,
                first_seen_at=now, last_seen_at=now, run_id=run_id,
            )
        )


def upsert_record(
    session: Session, cand: CandidateRecord, score: ScoreResult, run_id: Optional[int]
) -> UpsertResult:
    rec_in = cand.record
    now = utcnow()
    existing = find_existing(session, cand)
    if existing is None:
        record = Record(
            title=rec_in.title,
            source_name=rec_in.source_name,
            url=rec_in.url,
            url_fingerprint=cand.url_fp,
            content_fingerprint=cand.content_fp,
            description=rec_in.description,
            metadata_json=rec_in.metadata_dict(),
            relevance_score=score.score,
            explanation=score.explanation,
            status="new",
            discovered_at=now,
            last_seen_at=now,
            first_run_id=run_id,
        )
        session.add(record)
        session.flush()
        session.add(StatusEvent(record_id=record.id, old_status=None, new_status="new", actor="system", reason="discovered"))
        created = True
    else:
        record = existing
        record.last_seen_at = now
        # Keep the original title/URL; refresh volatile fields only when non-empty.
        if rec_in.description:
            record.description = rec_in.description
        merged = dict(record.metadata_json or {})
        merged.update(rec_in.metadata_dict())
        record.metadata_json = merged
        record.relevance_score = score.score
        record.explanation = score.explanation
        created = False

    _add_source(session, record, cand.url_fp, rec_in.url, rec_in.source_name, run_id)
    for fp, url in cand.alt_urls.items():
        _add_source(session, record, fp, url, rec_in.source_name, run_id)
    session.add(
        RecordScore(
            record_id=record.id,
            run_id=run_id,
            score=score.score,
            agent_score=score.agent_score,
            criteria_json={"criteria": score.criteria, "excluded": score.excluded},
            explanation=score.explanation,
        )
    )
    session.flush()
    return UpsertResult(record=record, created=created)


def get_record(session: Session, record_id: int) -> Optional[Record]:
    return session.get(Record, record_id)


def list_records(
    session: Session, *, status: Optional[str] = None, limit: int = 10, exclude_statuses: Sequence[str] = ()
) -> List[Record]:
    q = select(Record)
    if status:
        q = q.where(Record.status == status)
    elif exclude_statuses:
        q = q.where(Record.status.not_in(list(exclude_statuses)))
    q = q.order_by(Record.discovered_at.desc(), Record.id.desc()).limit(limit)
    return list(session.scalars(q))


def records_for_prompt(session: Session, limit: int) -> List[Record]:
    if limit <= 0:
        return []
    q = select(Record).order_by(Record.last_seen_at.desc(), Record.id.desc()).limit(limit)
    return list(session.scalars(q))


def pending_notifications(session: Session, threshold: float, limit: int) -> List[Record]:
    """Records that qualify for a notification but have not been notified yet."""
    if limit <= 0:
        return []
    latest_excluded = _excluded_record_ids(session)
    q = (
        select(Record)
        .where(Record.notified_at.is_(None), Record.status == "new", Record.relevance_score >= threshold)
        .order_by(Record.relevance_score.desc(), Record.id)
    )
    out = [r for r in session.scalars(q) if r.id not in latest_excluded]
    return out[:limit]


def _excluded_record_ids(session: Session) -> set:
    """IDs of records whose most recent score marked them as excluded."""
    latest = (
        select(RecordScore.record_id, func.max(RecordScore.id).label("max_id"))
        .group_by(RecordScore.record_id)
        .subquery()
    )
    rows = session.execute(select(RecordScore).join(latest, RecordScore.id == latest.c.max_id)).scalars()
    return {s.record_id for s in rows if (s.criteria_json or {}).get("excluded")}


def mark_notified(session: Session, record: Record) -> None:
    record.notified_at = utcnow()
    session.add(TrackedAction(record_id=record.id, action="notify", actor="system", details_json={}))
    session.flush()


def set_status(
    session: Session, record: Record, new_status: str, *, allowed: Sequence[str], actor: str, reason: str = ""
) -> bool:
    """Change a record's status, recording history. Returns False if unchanged."""
    if new_status not in allowed:
        raise InvalidStatusError(new_status)
    if record.status == new_status:
        return False
    session.add(
        StatusEvent(record_id=record.id, old_status=record.status, new_status=new_status, actor=actor, reason=reason[:200])
    )
    record.status = new_status
    session.flush()
    return True


def track_action(
    session: Session, record_id: Optional[int], action: str, actor: str, details: Optional[Dict[str, Any]] = None
) -> None:
    session.add(TrackedAction(record_id=record_id, action=action, actor=actor, details_json=details or {}))
    session.flush()


def add_feedback(session: Session, record_id: int, user_id: str, feedback: str, comment: str = "") -> None:
    session.add(UserFeedback(record_id=record_id, user_id=user_id, feedback=feedback, comment=comment))
    session.flush()


def feedback_examples(session: Session, feedback: str, limit: int = 20) -> List[Record]:
    q = (
        select(Record)
        .join(UserFeedback, UserFeedback.record_id == Record.id)
        .where(UserFeedback.feedback == feedback)
        .order_by(UserFeedback.id.desc())
        .limit(limit)
    )
    seen, out = set(), []
    for r in session.scalars(q):
        if r.id not in seen:
            seen.add(r.id)
            out.append(r)
    return out


def status_counts(session: Session) -> List[Tuple[str, int]]:
    rows = session.execute(select(Record.status, func.count()).group_by(Record.status).order_by(Record.status))
    return [(s, int(n)) for s, n in rows]


def runs_since(session: Session, since: dt.datetime) -> int:
    return int(session.scalar(select(func.count()).select_from(ResearchRun).where(ResearchRun.started_at >= since)) or 0)
