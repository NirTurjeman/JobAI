"""Wiring: build settings, config, database and runner from the environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .agents.factory import create_agent
from .config import ResearchConfig, Settings, load_research_config, load_settings
from .db.session import make_engine, make_session_factory
from .locking import RunLock
from .logging_setup import RedactingFilter
from .notify.base import Notifier, NullNotifier
from .runner import ResearchRunner


@dataclass
class Services:
    settings: Settings
    cfg: ResearchConfig
    engine: Engine
    session_factory: sessionmaker
    redactor: RedactingFilter

    @property
    def lock(self) -> RunLock:
        return RunLock(self.settings.resolved_lock_file)


def build_services(settings: Optional[Settings] = None, config_path: Optional[Path] = None) -> Services:
    settings = settings or load_settings()
    cfg = load_research_config(config_path or settings.research_config)
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = make_engine(settings.resolved_database_url)
    return Services(
        settings=settings,
        cfg=cfg,
        engine=engine,
        session_factory=make_session_factory(engine),
        redactor=RedactingFilter(settings.secret_values()),
    )


def make_notifier(settings: Settings) -> Notifier:
    if not settings.notifications_enabled:
        return NullNotifier()
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    chat_id = settings.notification_chat_id
    if not token or not chat_id:
        return NullNotifier()
    from .notify.telegram_notifier import TelegramNotifier

    return TelegramNotifier(token, chat_id)


def make_runner(
    services: Services,
    *,
    backend: Optional[str] = None,
    notify: bool = True,
    notifier: Optional[Notifier] = None,
) -> ResearchRunner:
    return ResearchRunner(
        services.cfg,
        services.session_factory,
        create_agent(services.settings, services.cfg, backend),
        notifier or make_notifier(services.settings),
        lock=services.lock,
        redact=services.redactor.redact,
        notify=notify,
    )
