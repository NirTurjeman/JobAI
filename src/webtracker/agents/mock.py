"""Offline agent returning fixture data. Used for development and tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .base import AgentExecutionError, AgentResponse, ResearchAgent, ResearchTask


class MockAgent(ResearchAgent):
    name = "mock"

    def __init__(
        self,
        fixture: Optional[Path] = None,
        *,
        payload: Optional[Dict[str, Any]] = None,
        raw_output: Optional[str] = None,
        fail_with: Optional[str] = None,
    ) -> None:
        self.fixture = fixture
        self.payload = payload
        self.raw_output = raw_output
        self.fail_with = fail_with
        self.calls: list = []

    def research(self, task: ResearchTask) -> AgentResponse:
        self.calls.append(task)
        if self.fail_with:
            raise AgentExecutionError(self.fail_with)
        if self.raw_output is not None:
            raw = self.raw_output
        elif self.payload is not None:
            raw = json.dumps(self.payload)
        elif self.fixture is not None:
            try:
                raw = Path(self.fixture).read_text(encoding="utf-8")
            except OSError as exc:
                raise AgentExecutionError(f"cannot read mock fixture: {exc}") from exc
        else:
            raw = json.dumps({"records": [], "sources_consulted": [], "notes": "empty mock"})
        return AgentResponse(raw_output=raw, backend=self.name, diagnostics={"fixture": str(self.fixture or "")})
