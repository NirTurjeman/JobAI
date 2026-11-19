"""Select the research agent backend from settings."""

from __future__ import annotations

from typing import Optional

from ..config import ResearchConfig, Settings
from .base import ResearchAgent
from .codex import CodexAgent
from .mock import MockAgent


def create_agent(settings: Settings, cfg: ResearchConfig, backend: Optional[str] = None) -> ResearchAgent:
    backend = backend or settings.agent_backend
    if backend == "mock":
        return MockAgent(fixture=settings.mock_fixture)
    if backend == "codex":
        return CodexAgent(
            binary=settings.codex_binary,
            model=settings.codex_model,
            profile=settings.codex_profile,
            timeout_seconds=settings.codex_timeout_seconds,
            codex_home=settings.codex_home,
            browser=cfg.browser,
        )
    raise ValueError(f"unknown agent backend: {backend!r}")
