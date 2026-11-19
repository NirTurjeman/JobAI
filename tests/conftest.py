import json
from pathlib import Path

import pytest

from webtracker.config import Settings, load_research_config
from webtracker.db.session import make_engine, make_session_factory, upgrade_database
from webtracker.logging_setup import RedactingFilter
from webtracker.services import Services

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = ROOT / "config" / "research.example.yaml"
MOCK_FIXTURE = ROOT / "config" / "mock" / "mock_results.json"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # Never pick up a developer's .env or real secrets during tests.
    for key in list(__import__("os").environ):
        if key.startswith("WT_") or key == "CREDENTIALS_DIRECTORY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WT_ENV_FILE", str(tmp_path / "nonexistent.env"))


@pytest.fixture
def cfg():
    return load_research_config(EXAMPLE_CONFIG)


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
    upgrade_database(url)
    return url


@pytest.fixture
def session_factory(db_url):
    engine = make_engine(db_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def settings(tmp_path, db_url):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=db_url,
        research_config=EXAMPLE_CONFIG,
        telegram_allowed_user_id=111,
        scan_cooldown_seconds=0,
    )


@pytest.fixture
def services(settings, cfg, session_factory):
    return Services(
        settings=settings,
        cfg=cfg,
        engine=session_factory.kw["bind"],
        session_factory=session_factory,
        redactor=RedactingFilter(settings.secret_values()),
    )


@pytest.fixture
def mock_payload():
    return json.loads(MOCK_FIXTURE.read_text())


def make_record(**overrides):
    base = {
        "title": "Sample item",
        "url": "https://example.com/item/1",
        "source_name": "Example",
        "description": "about example topic",
        "relevance_score": 0.8,
        "explanation": "fits",
        "metadata": [],
        "additional_urls": [],
    }
    base.update(overrides)
    return base
