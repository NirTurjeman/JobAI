"""Configuration loading.

Two layers:

* ``Settings`` — runtime/deployment settings and secrets, read from environment
  variables (prefix ``WT_``), an optional ``.env`` file, and secret files
  (``WT_SECRETS_DIR`` or systemd's ``$CREDENTIALS_DIRECTORY``).
* ``ResearchConfig`` — the application-specific research definition (objective,
  queries, criteria, statuses...) loaded from a YAML file. Nothing about a
  particular research domain is hard-coded in the code base.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_STATUSES = ["new", "viewed", "saved", "in_progress", "done", "dismissed"]
STATUS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _secrets_dir() -> Optional[str]:
    for candidate in (os.environ.get("WT_SECRETS_DIR"), os.environ.get("CREDENTIALS_DIRECTORY")):
        if candidate and Path(candidate).is_dir():
            return candidate
    return None


class Settings(BaseSettings):
    """Deployment settings. Secrets are ``SecretStr`` so they never print."""

    model_config = SettingsConfigDict(
        env_prefix="WT_",
        env_file=os.environ.get("WT_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    data_dir: Path = Path("data")
    database_url: Optional[str] = None
    research_config: Path = Path("config/research.yaml")
    lock_file: Optional[Path] = None
    log_level: str = "INFO"

    agent_backend: Literal["mock", "codex"] = "mock"
    mock_fixture: Optional[Path] = None

    codex_binary: str = "codex"
    codex_model: Optional[str] = None
    codex_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    codex_profile: Optional[str] = None
    codex_home: Optional[Path] = None

    telegram_bot_token: Optional[SecretStr] = None
    telegram_allowed_user_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None
    notifications_enabled: bool = True
    scan_cooldown_seconds: int = Field(default=1800, ge=0)

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING or ERROR")
        return v

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'webtracker.sqlite3').as_posix()}"

    @property
    def resolved_lock_file(self) -> Path:
        return self.lock_file or (self.data_dir / "research.lock")

    @property
    def notification_chat_id(self) -> Optional[int]:
        # Private chats with a bot share the user's ID, so default to it.
        return self.telegram_chat_id or self.telegram_allowed_user_id

    def secret_values(self) -> List[str]:
        """Return secret strings so logging can redact them."""
        out = []
        if self.telegram_bot_token:
            out.append(self.telegram_bot_token.get_secret_value())
        return [s for s in out if s]


def load_settings(**overrides) -> Settings:
    secrets = _secrets_dir()
    if secrets:
        return Settings(_secrets_dir=secrets, **overrides)  # type: ignore[call-arg]
    return Settings(**overrides)


# --------------------------------------------------------------------------- research config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Criterion(_Strict):
    """A configurable evaluation criterion.

    ``description`` is given to the agent; the keyword lists are evaluated
    deterministically on the agent's output (title, description, metadata).
    """

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    weight: float = Field(default=1.0, gt=0, le=100)
    include_keywords: List[str] = Field(default_factory=list, max_length=200)
    exclude_keywords: List[str] = Field(default_factory=list, max_length=200)
    required: bool = False

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def _kw(cls, v: List[str]) -> List[str]:
        return [k.strip().lower() for k in v if k and k.strip()]


class MetadataField(_Strict):
    key: str
    description: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError("metadata key must be snake_case (a-z, 0-9, _)")
        return v


class ScoringConfig(_Strict):
    agent_weight: float = Field(default=0.7, ge=0, le=1)
    notify_min_score: float = Field(default=0.6, ge=0, le=1)
    store_min_score: float = Field(default=0.0, ge=0, le=1)


class LimitsConfig(_Strict):
    max_records_per_run: int = Field(default=25, ge=1, le=200)
    max_notifications_per_run: int = Field(default=10, ge=0, le=100)
    existing_records_in_prompt: int = Field(default=100, ge=0, le=1000)


class BrowserConfig(_Strict):
    """Optional browser tooling made available to the agent (Codex MCP server)."""

    enabled: bool = False
    mcp_command: str = "npx"
    mcp_args: List[str] = Field(default_factory=lambda: ["@playwright/mcp@latest", "--headless", "--isolated"])


class SourcesConfig(_Strict):
    seed_sources: List[str] = Field(default_factory=list, max_length=100)
    allow_discovery: bool = True
    blocked_domains: List[str] = Field(default_factory=list, max_length=500)

    @field_validator("blocked_domains")
    @classmethod
    def _domains(cls, v: List[str]) -> List[str]:
        return [d.strip().lower().lstrip(".") for d in v if d.strip()]


class ResearchConfig(_Strict):
    name: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=4000)
    search_queries: List[str] = Field(default_factory=list, max_length=50)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    criteria: List[Criterion] = Field(default_factory=list, max_length=50)
    metadata_fields: List[MetadataField] = Field(default_factory=list, max_length=30)
    statuses: List[str] = Field(default_factory=lambda: list(DEFAULT_STATUSES))
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    language: str = Field(default="English", max_length=40)
    prompt_template: Optional[Path] = None
    extra_instructions: str = Field(default="", max_length=4000)

    @field_validator("statuses")
    @classmethod
    def _statuses(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("statuses must not be empty")
        for s in v:
            if not STATUS_RE.match(s):
                raise ValueError(f"invalid status name: {s!r}")
        if len(set(v)) != len(v):
            raise ValueError("statuses must be unique")
        for required in ("new", "dismissed"):
            if required not in v:
                raise ValueError(f"statuses must include {required!r}")
        return v

    @model_validator(mode="after")
    def _unique_criteria(self) -> "ResearchConfig":
        names = [c.name for c in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("criterion names must be unique")
        return self

    # These are filled by the loader so the prompt template can be resolved
    # relative to the YAML file.
    _source_path: Optional[Path] = None
    _raw_hash: str = ""

    @property
    def config_hash(self) -> str:
        return self._raw_hash

    def resolve_prompt_template(self) -> Optional[Path]:
        if self.prompt_template is None:
            return None
        p = self.prompt_template
        if not p.is_absolute() and self._source_path is not None:
            p = self._source_path.parent / p
        return p


def load_research_config(path: Path) -> ResearchConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"research config not found: {path}")
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"research config must be a mapping: {path}")
    try:
        cfg = ResearchConfig.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(f"invalid research config {path}:\n{exc}") from exc
    cfg._source_path = path.resolve()
    cfg._raw_hash = hashlib.sha256(raw).hexdigest()[:16]
    return cfg


def statuses_index(cfg: ResearchConfig) -> Dict[str, int]:
    return {s: i for i, s in enumerate(cfg.statuses)}
