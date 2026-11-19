"""Orchestrates one research run: agent → validation → dedup → scoring → storage → notification."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from .agents.base import AgentExecutionError, ResearchAgent, ResearchTask
from .config import ResearchConfig
from .db import repository as repo
from .db.session import session_scope
from .dedup import dedupe_batch
from .locking import LockHeldError, RunLock
from .notify.base import Notifier
from .prompt import build_prompt
from .sanitize import validate_public_url
from .schemas import AgentEnvelope, AgentRecord, codex_output_schema
from .scoring import evaluate

log = logging.getLogger(__name__)

MAX_LOGGED_VALIDATION_ERRORS = 20


@dataclass
class RunSummary:
    status: str  # succeeded|partial|failed|skipped
    run_id: Optional[int] = None
    returned: int = 0
    new: int = 0
    updated: int = 0
    invalid: int = 0
    below_threshold: int = 0
    notified: int = 0
    message: str = ""
    new_record_ids: List[int] = field(default_factory=list)

    def as_text(self) -> str:
        if self.status == "skipped":
            return f"Run skipped: {self.message}"
        return (
            f"Run {self.run_id} {self.status}: {self.returned} returned, {self.new} new, "
            f"{self.updated} updated, {self.invalid} invalid, {self.notified} notified"
            + (f" — {self.message}" if self.message else "")
        )


class ResearchRunner:
    def __init__(
        self,
        cfg: ResearchConfig,
        session_factory: sessionmaker,
        agent: ResearchAgent,
        notifier: Notifier,
        *,
        lock: Optional[RunLock] = None,
        redact: Callable[[str], str] = lambda s: s,
        notify: bool = True,
    ) -> None:
        self.cfg = cfg
        self.sf = session_factory
        self.agent = agent
        self.notifier = notifier
        self.lock = lock
        self.redact = redact
        self.notify = notify

    # ------------------------------------------------------------------ public

    def run(self, trigger: str = "manual") -> RunSummary:
        if self.lock is not None:
            try:
                self.lock.acquire()
            except LockHeldError as exc:
                log.warning("%s", exc)
                return RunSummary(status="skipped", message="another research run is in progress")
        try:
            return self._run(trigger)
        finally:
            if self.lock is not None:
                self.lock.release()

    # ------------------------------------------------------------------ internals

    def _error(self, run_id: int, stage: str, exc_type: str, message: str, excerpt: Optional[str] = None) -> None:
        with session_scope(self.sf) as s:
            repo.log_error(s, run_id, stage, exc_type, self.redact(message), self.redact(excerpt) if excerpt else None)

    def _finish(self, summary: RunSummary, status: str, message: str = "") -> RunSummary:
        summary.status = status
        summary.message = message
        with session_scope(self.sf) as s:
            run = s.get(repo.ResearchRun, summary.run_id)
            run.records_returned = summary.returned
            run.records_new = summary.new
            run.records_updated = summary.updated
            run.records_invalid = summary.invalid
            run.notifications_sent = summary.notified
            repo.finish_run(s, run, status, self.redact(message) if message else None)
        log.info("%s", summary.as_text())
        return summary

    def _run(self, trigger: str) -> RunSummary:
        cfg = self.cfg
        with session_scope(self.sf) as s:
            run = repo.start_run(
                s, trigger=trigger, agent_backend=self.agent.name, config_name=cfg.name, config_hash=cfg.config_hash
            )
            run_id = run.id
            prompt = build_prompt(
                cfg,
                repo.records_for_prompt(s, cfg.limits.existing_records_in_prompt),
                liked=repo.feedback_examples(s, "relevant"),
                disliked=repo.feedback_examples(s, "not_relevant"),
            )
        summary = RunSummary(status="running", run_id=run_id)
        log.info("research run %s started (trigger=%s, agent=%s)", run_id, trigger, self.agent.name)

        # 1. Agent execution -------------------------------------------------
        task = ResearchTask(prompt=prompt, config=cfg, output_schema=codex_output_schema(), run_id=run_id)
        try:
            response = self.agent.research(task)
        except AgentExecutionError as exc:
            self._error(run_id, "agent", "AgentExecutionError", str(exc), exc.excerpt)
            return self._finish(summary, "failed", f"agent failed: {exc}")
        except Exception as exc:  # defensive: an agent bug must not crash the service
            log.exception("unexpected agent error")
            self._error(run_id, "agent", type(exc).__name__, str(exc))
            return self._finish(summary, "failed", f"agent error: {type(exc).__name__}")

        # 2. Parse + envelope validation --------------------------------------
        try:
            data = json.loads(response.raw_output)
            envelope = AgentEnvelope.model_validate(data)
        except (ValueError, ValidationError) as exc:
            self._error(run_id, "parse", type(exc).__name__, str(exc)[:2000], response.raw_output[:2000])
            return self._finish(summary, "failed", "agent output is not valid JSON for the schema")

        summary.returned = len(envelope.records)

        # 3. Per-record validation --------------------------------------------
        valid: List[AgentRecord] = []
        for i, raw in enumerate(envelope.records):
            try:
                rec = AgentRecord.model_validate(raw)
                validate_public_url(rec.url, cfg.sources.blocked_domains)
                valid.append(rec)
            except (ValidationError, ValueError) as exc:
                summary.invalid += 1
                if summary.invalid <= MAX_LOGGED_VALIDATION_ERRORS:
                    self._error(run_id, "validation", type(exc).__name__, f"record {i}: {exc}"[:2000],
                                json.dumps(raw, ensure_ascii=False)[:1000])
        valid = valid[: cfg.limits.max_records_per_run]

        # 4. Dedup, scoring, storage -------------------------------------------
        candidates = dedupe_batch(valid)
        with session_scope(self.sf) as s:
            for cand in candidates:
                score = evaluate(cand.record, cfg)
                if score.score < cfg.scoring.store_min_score:
                    summary.below_threshold += 1
                    continue
                try:
                    with s.begin_nested():
                        result = repo.upsert_record(s, cand, score, run_id)
                except Exception as exc:
                    log.exception("failed to store record")
                    summary.invalid += 1
                    repo.log_error(s, run_id, "storage", type(exc).__name__, self.redact(str(exc))[:2000], cand.record.url)
                    continue
                if result.created:
                    summary.new += 1
                    summary.new_record_ids.append(result.record.id)
                else:
                    summary.updated += 1

        # 5. Notifications ------------------------------------------------------
        notify_failed = 0
        if self.notify:
            with session_scope(self.sf) as s:
                pending = repo.pending_notifications(
                    s, cfg.scoring.notify_min_score, cfg.limits.max_notifications_per_run
                )
                if pending:
                    try:
                        sent_ids = set(self.notifier.send_records(pending, cfg.statuses))
                    except Exception as exc:
                        log.warning("notifier failed: %s", type(exc).__name__)
                        sent_ids = set()
                        repo.log_error(s, run_id, "notify", type(exc).__name__, self.redact(str(exc))[:2000])
                    for rec in pending:
                        if rec.id in sent_ids:
                            repo.mark_notified(s, rec)
                    summary.notified = len(sent_ids)
                    notify_failed = len(pending) - len(sent_ids)
                    if notify_failed:
                        repo.log_error(s, run_id, "notify", "NotificationIncomplete",
                                       f"{notify_failed} notification(s) not delivered; will retry next run")

        status = "partial" if (summary.invalid or notify_failed) else "succeeded"
        return self._finish(summary, status)
