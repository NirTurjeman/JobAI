"""Agent interface. Any research backend (mock, Codex, others) implements ``ResearchAgent``."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict

from ..config import ResearchConfig


class AgentExecutionError(Exception):
    """The agent failed to produce output (crash, timeout, missing output...)."""

    def __init__(self, message: str, *, excerpt: str = "") -> None:
        super().__init__(message)
        self.excerpt = excerpt


@dataclass
class ResearchTask:
    prompt: str
    config: ResearchConfig
    output_schema: Dict[str, Any]
    run_id: int


@dataclass
class AgentResponse:
    """Raw agent output. It is untrusted and is validated by the runner."""

    raw_output: str
    backend: str
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class ResearchAgent(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def research(self, task: ResearchTask) -> AgentResponse:
        """Perform research and return the raw JSON text of an ``AgentEnvelope``."""
