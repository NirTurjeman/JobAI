# webtracker — autonomous web research & tracking

A self-hosted framework that periodically researches public web sources with an
autonomous agent (OpenAI Codex CLI with live web search), extracts structured
records, scores them against configurable criteria, stores them in SQLite and
notifies you on Telegram.

Nothing domain-specific is hard-coded: the research objective, queries, criteria,
metadata fields and statuses all live in a YAML file. See
[docs/architecture.md](docs/architecture.md) for the design.

## Features

- Agent interface with a **mock** backend (offline) and a **Codex** backend
  (`codex exec --sandbox read-only`, live web search, optional Playwright MCP browser)
- Strict Pydantic validation of agent output, per-record error isolation
- Deterministic deduplication (canonical URL + title/domain fingerprints)
- SQLite + SQLAlchemy 2 + Alembic migrations; status history and audit trail
- Telegram bot (`/start /records /status /scan /help`, View/Save/Dismiss/Update status buttons),
  locked to one Telegram user ID
- systemd timer at 08:00 and 16:00 Asia/Jerusalem, file lock against overlapping runs

## Requirements

- Python ≥ 3.9 (Linux x86_64/ARM64; macOS also works for development)
- [Codex CLI](https://github.com/openai/codex) logged in (`codex login`) for the live agent
- Optional: Node.js ≥ 18 if you enable the Playwright MCP browser tool

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
cp config/research.example.yaml config/research.yaml   # then edit it
.venv/bin/webtracker check-config
.venv/bin/webtracker init-db
```

`.env`, `config/research.yaml` and `data/` are git-ignored.

### Run with mock data (offline)

```bash
.venv/bin/webtracker run --agent mock --no-notify
```

### Run with Codex

```bash
.venv/bin/webtracker print-prompt        # inspect what will be sent
.venv/bin/webtracker run --agent codex --no-notify
```

### Telegram

1. Create a bot with @BotFather and put the token in `WT_TELEGRAM_BOT_TOKEN`.
2. Put your numeric Telegram user ID in `WT_TELEGRAM_ALLOWED_USER_ID`.
   Every other user (and every group chat) is refused.
3. Start the bot: `.venv/bin/webtracker bot`, then send `/start` in a private chat.

Leave the token empty to disable notifications entirely.

### Development scheduler

```bash
.venv/bin/webtracker next-runs           # shows the next run times
.venv/bin/webtracker dev-scheduler       # runs at 08:00/16:00 Asia/Jerusalem until stopped
```

### Tests

```bash
.venv/bin/pytest
```

## Commands

| Command | Purpose |
|---|---|
| `webtracker check-config` | Validate settings and the research YAML |
| `webtracker init-db` | Create/upgrade the database (Alembic `upgrade head`) |
| `webtracker run [--agent mock\|codex] [--trigger manual\|timer] [--no-notify]` | One research run (exit 75 if another run holds the lock) |
| `webtracker bot` | Telegram bot (long polling) |
| `webtracker dev-scheduler` | Development scheduler loop |
| `webtracker next-runs` / `print-prompt` / `print-schema` | Inspection helpers |

## Configuration

### Settings (environment, prefix `WT_`)

| Variable | Default | Notes |
|---|---|---|
| `WT_DATA_DIR` | `data` | DB and lock file location |
| `WT_DATABASE_URL` | `sqlite:///<data>/webtracker.sqlite3` | |
| `WT_RESEARCH_CONFIG` | `config/research.yaml` | |
| `WT_AGENT_BACKEND` | `mock` | `mock` or `codex` |
| `WT_MOCK_FIXTURE` | — | JSON file returned by the mock agent |
| `WT_CODEX_BINARY` / `WT_CODEX_MODEL` / `WT_CODEX_PROFILE` / `WT_CODEX_HOME` | `codex` / — | |
| `WT_CODEX_TIMEOUT_SECONDS` | `900` | |
| `WT_TELEGRAM_BOT_TOKEN` | — | secret; prefer a systemd credential |
| `WT_TELEGRAM_ALLOWED_USER_ID` | — | the only user allowed to use the bot |
| `WT_TELEGRAM_CHAT_ID` | allowed user ID | where notifications go |
| `WT_NOTIFICATIONS_ENABLED` | `true` | |
| `WT_SCAN_COOLDOWN_SECONDS` | `1800` | minimum gap between `/scan` runs |

Secrets can also be read from files: a directory given by `WT_SECRETS_DIR`, or
systemd's `$CREDENTIALS_DIRECTORY` (files named after the variable, e.g.
`WT_TELEGRAM_BOT_TOKEN`).

### Research config (YAML)

See [config/research.example.yaml](config/research.example.yaml). Key sections:
`objective`, `search_queries`, `sources` (seed sources, discovery, blocked domains),
`criteria` (description for the agent + optional include/exclude keywords, weight,
required), `metadata_fields`, `statuses`, `scoring` (agent weight, notify/store
thresholds), `limits`, `browser`, `prompt_template`, `extra_instructions`.

Final score = `agent_weight × agent relevance + (1 − agent_weight) × keyword score`
(keyword score = weighted share of keyword criteria that matched). A record that hits
an exclude keyword or misses a `required` criterion is stored but never notified.

The task template (`src/webtracker/templates/research_task.md`) can be replaced via
`prompt_template`; the safety rules and the untrusted-data block are always added by
code and cannot be removed by a template.

## Security model

- **Untrusted content**: all agent output is validated with strict schemas
  (`extra="forbid"`, length limits, control/bidi characters stripped). URLs must be
  public `http(s)` — no credentials, localhost, private/link-local IPs, or blocked domains.
- **Prompt injection**: fixed safety rules come first; data derived from the web
  (known record titles, feedback) is JSON-encoded inside a block fenced with a random
  per-run nonce, and marker sequences are neutralised.
- **No command execution from model output**: output is only parsed as JSON. Codex is
  invoked with an argument list (no shell), prompt on stdin, `--sandbox read-only`,
  `--ephemeral`, inside an empty temp directory; `--dangerously-*`/`danger-full-access`
  are refused in code.
- **Secret isolation**: Codex gets an allowlisted environment (no Telegram token); logs
  pass through a redacting filter; `httpx` request logging (which would include the bot
  token in URLs) is silenced; stored error messages are redacted.
- **Telegram**: only the configured user ID in a private chat; callback data is strictly
  parsed (`^[vsdut]:\d+(:\d+)?$`); free text is never interpreted; all record text is
  HTML-escaped.
- **No outbound actions** other than Telegram notifications to the configured chat.
- **Least privilege**: DB and lock files are created `0600`; systemd units run as a
  dedicated user with `ProtectSystem=strict`, `NoNewPrivileges`, `UMask=0077`.

## Raspberry Pi 5 deployment (not yet performed)

Tested target: Raspberry Pi OS Bookworm 64-bit (Python 3.11, systemd 252).

```bash
# 1. System user and directories
sudo useradd --system --create-home --home-dir /home/webtracker --shell /usr/sbin/nologin webtracker
sudo install -d -o webtracker -g webtracker -m 0700 /var/lib/webtracker
sudo install -d -o root -g webtracker -m 0750 /etc/webtracker
sudo install -d -o root -g root -m 0700 /etc/webtracker/credentials

# 2. Code and virtualenv
sudo git clone <your repo> /opt/webtracker        # or rsync the project
sudo chown -R root:webtracker /opt/webtracker
cd /opt/webtracker && sudo python3 -m venv .venv && sudo .venv/bin/pip install .

# 3. Codex CLI (ARM64 build) and login as the service user
#    install per the Codex docs, then:
sudo -u webtracker -H codex login

# 4. Configuration and secrets
sudo install -o root -g webtracker -m 0640 deploy/systemd/webtracker.env.example /etc/webtracker/webtracker.env
sudo install -o root -g webtracker -m 0640 config/research.example.yaml /etc/webtracker/research.yaml
sudo sh -c 'umask 077; printf %s "<bot token>" > /etc/webtracker/credentials/telegram_bot_token'
sudoedit /etc/webtracker/webtracker.env /etc/webtracker/research.yaml

# 5. Database
sudo -u webtracker env $(grep -v '^#' /etc/webtracker/webtracker.env | xargs) /opt/webtracker/.venv/bin/webtracker init-db

# 6. Services
sudo cp deploy/systemd/webtracker-*.service deploy/systemd/webtracker-research.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now webtracker-bot.service webtracker-research.timer
systemctl list-timers webtracker-research.timer
```

Manual run on the Pi: `sudo systemctl start webtracker-research.service`;
logs: `journalctl -u webtracker-research -u webtracker-bot`.

Adjust `ReadWritePaths` if Codex keeps its state outside `/home/webtracker/.codex`.
If Codex's Linux sandbox cannot initialise under the systemd hardening, check
`journalctl` and relax only the specific directive involved.

## Database schema

`records`, `record_sources`, `record_scores`, `tracked_actions`, `status_events`,
`research_runs`, `agent_errors`, `user_feedback` — see
[src/webtracker/db/models.py](src/webtracker/db/models.py). After changing models,
create a migration:

```bash
.venv/bin/python -c "from alembic import command; from webtracker.db.session import alembic_config; command.revision(alembic_config('sqlite:///data/webtracker.sqlite3'), message='describe change', autogenerate=True)"
```
