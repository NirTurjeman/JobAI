"""Strict schemas for agent output.

The agent's output is untrusted. The envelope is validated first; each record
is then validated individually so a single malformed record does not discard
a whole run. All string fields are cleaned (control/bidi characters removed,
length clamped) and URLs must be public http(s) URLs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .sanitize import clean_text, validate_public_url

MetadataValue = Union[str, int, float, bool, None]

MAX_METADATA_ITEMS = 30


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetadataItem(_Strict):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=1000)


class AgentRecord(_Strict):
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    source_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    relevance_score: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(default="", max_length=4000)
    metadata: List[MetadataItem] = Field(default_factory=list, max_length=MAX_METADATA_ITEMS)
    additional_urls: List[str] = Field(default_factory=list, max_length=10)

    @field_validator("title", "source_name")
    @classmethod
    def _single_line(cls, v: str) -> str:
        v = clean_text(v, 300, single_line=True)
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        return clean_text(v, 2000)

    @field_validator("explanation")
    @classmethod
    def _expl(cls, v: str) -> str:
        return clean_text(v, 1000)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return validate_public_url(v)

    @field_validator("additional_urls")
    @classmethod
    def _urls(cls, v: List[str]) -> List[str]:
        out = []
        for u in v:
            try:
                out.append(validate_public_url(u))
            except ValueError:
                continue  # silently drop unsafe secondary URLs
        return out

    def metadata_dict(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for item in self.metadata:
            key = clean_text(item.key, 64, single_line=True).lower().replace(" ", "_")
            if key and key not in out:
                out[key] = clean_text(item.value, 500, single_line=True)
        return out


class SourceConsulted(_Strict):
    name: str = Field(max_length=200)
    url: str = Field(max_length=2048)


class AgentEnvelope(_Strict):
    """Top-level output. ``records`` is kept raw and validated per item."""

    records: List[Dict[str, Any]] = Field(max_length=500)
    sources_consulted: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=4000)


def codex_output_schema() -> Dict[str, Any]:
    """JSON Schema passed to ``codex exec --output-schema``.

    Written by hand to satisfy OpenAI strict structured-output rules (every
    property required, ``additionalProperties: false``, no free-form objects).
    """
    metadata_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "value"],
        "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
    }
    record = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title", "url", "source_name", "description", "relevance_score",
            "explanation", "metadata", "additional_urls",
        ],
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "source_name": {"type": "string"},
            "description": {"type": "string"},
            "relevance_score": {"type": "number"},
            "explanation": {"type": "string"},
            "metadata": {"type": "array", "items": metadata_item},
            "additional_urls": {"type": "array", "items": {"type": "string"}},
        },
    }
    source = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "url"],
        "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["records", "sources_consulted", "notes"],
        "properties": {
            "records": {"type": "array", "items": record},
            "sources_consulted": {"type": "array", "items": source},
            "notes": {"type": ["string", "null"]},
        },
    }
