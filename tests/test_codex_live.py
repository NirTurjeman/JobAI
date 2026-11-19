"""Opt-in live test against the real Codex CLI (uses your Codex account and network).

Run with:  WT_LIVE_CODEX=1 .venv/bin/pytest tests/test_codex_live.py -s
"""

import os

import pytest

from webtracker.agents.codex import CodexAgent
from webtracker.config import load_research_config
from webtracker.notify.base import MemoryNotifier
from webtracker.runner import ResearchRunner

pytestmark = pytest.mark.skipif(os.environ.get("WT_LIVE_CODEX") != "1", reason="set WT_LIVE_CODEX=1 to run")

_LIVE_CONFIG = """
name: live-smoke-test
objective: Find the most recent official Python programming language release announcements. One record per release.
search_queries: ["latest Python release announcement"]
metadata_fields:
  - {key: version, description: Release version number}
limits: {max_records_per_run: 3, max_notifications_per_run: 0}
"""


def test_live_codex_run(tmp_path, session_factory):
    p = tmp_path / "live.yaml"
    p.write_text(_LIVE_CONFIG)
    cfg = load_research_config(p)
    summary = ResearchRunner(cfg, session_factory, CodexAgent(timeout_seconds=900), MemoryNotifier()).run()
    print(summary.as_text())
    assert summary.status in ("succeeded", "partial"), summary.message
    assert summary.new >= 1
