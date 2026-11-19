# Architecture & implementation plan

## Environment findings (at project start)

| Item | Found |
|---|---|
| Repository | Empty directory, not a git repository |
| Host | macOS (Darwin 25.6, Apple Silicon arm64) — **not** the Linux x86_64 workstation described in the brief |
| Python | 3.9.6 (system) only — code targets Python ≥ 3.9 (Raspberry Pi OS Bookworm ships 3.11) |
| Codex CLI | `codex-cli 0.163.0`, logged in (`~/.codex/auth.json` present) |
| Node.js | Not installed → Playwright MCP browser tools cannot be exercised locally |
| systemd | Not available on macOS → unit files are validated by tests/static checks only |

## Component overview

```
             ┌────────────────────────── config/ (external, git-ignored copies) ──────────┐
             │ research.yaml (objective, queries, criteria, statuses, thresholds)         │
             │ prompts/research_task.md (prompt template)    .env / systemd credentials   │
             └───────────────┬────────────────────────────────────────────────────────────┘
                             │ Pydantic validation (extra="forbid")
                             ▼
  systemd timer ──► webtracker run ──► ResearchRunner ──────────────────────────────┐
  (08:00/16:00      (file lock,        1. load config                               │
   Asia/Jerusalem)   no overlap)       2. read existing records (DB)                │
                                       3. build prompt (untrusted data fenced)      │
  Telegram /scan ─► same runner        4. ResearchAgent.research(task) ──► Mock | Codex
  (in-process,                         5. strict Pydantic validation per record     │   codex exec --sandbox read-only
   cooldown)                           6. URL normalization + deterministic dedup   │   --output-schema, web_search=live
                                       7. criteria scoring                          │   minimal env, no secrets
                                       8. store (records, sources, scores, events)  │
                                       9. notify qualifying records ──► Notifier ───┘──► Telegram (only configured chat)
                                                                         │
  webtracker bot (long-polling) ◄── inline buttons View/Save/Dismiss/Status, /records /status /scan /help
            │  authorization: single configured Telegram user ID
            ▼
        SQLite (SQLAlchemy 2 + Alembic migrations)
```

## Package layout

```
src/webtracker/
  config.py           Settings (env/secret files) + ResearchConfig (YAML)
  schemas.py          Strict agent output schemas + Codex JSON schema
  sanitize.py         URL safety checks, text cleaning, normalization
  dedup.py            Fingerprints + batch/DB deduplication
  scoring.py          Configurable criteria evaluation
  prompt.py           Research prompt construction with injection guards
  agents/             ResearchAgent interface, MockAgent, CodexAgent, factory
  db/                 SQLAlchemy models, session, repository
  migrations/         Alembic environment + versions (shipped inside the package)
  notify/             Notifier interface, Telegram notifier, message formatting
  bot/                Telegram bot handlers + authorization
  runner.py           Orchestration of a research run
  locking.py          Non-blocking file lock (prevents overlapping runs)
  scheduler.py        Schedule computation + development scheduler loop
  logging_setup.py    Secret-redacting logging
  cli.py              `webtracker` command line
deploy/systemd/       Service + timer units, env example
config/               Example config, prompt template, mock fixture
tests/                pytest suite
```

## Plan

1. Core: config, schemas, DB models + initial Alembic migration.
2. Agent interface + MockAgent + runner + dedup + scoring + notifier interface.
3. Telegram bot + Telegram notifier, scheduler, locking.
4. Tests and a full mock end-to-end run (no network).
5. CodexAgent: command construction tested with a fake `codex` binary, then one small live run.
6. README, systemd units, deployment notes. No Pi deployment, no remote push.
