"""Command-line entry point: ``webtracker <command>``."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .config import ConfigError, load_settings
from .logging_setup import setup_logging

log = logging.getLogger("webtracker")

EXIT_OK, EXIT_FAILED, EXIT_SKIPPED, EXIT_CONFIG = 0, 1, 75, 78  # 75 = EX_TEMPFAIL, 78 = EX_CONFIG


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="webtracker", description="Autonomous web research and tracking")
    p.add_argument("--config", type=Path, help="research config YAML (default: WT_RESEARCH_CONFIG)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or upgrade the database (Alembic migrations)")
    sub.add_parser("check-config", help="validate settings and research config")

    run = sub.add_parser("run", help="execute one research run")
    run.add_argument("--agent", choices=["mock", "codex"], help="override WT_AGENT_BACKEND")
    run.add_argument("--trigger", choices=["manual", "timer"], default="manual")
    run.add_argument("--no-notify", action="store_true", help="store results but send no notifications")

    sub.add_parser("bot", help="run the Telegram bot (long polling)")

    dev = sub.add_parser("dev-scheduler", help="run research at the scheduled times (development only)")
    dev.add_argument("--agent", choices=["mock", "codex"])

    sub.add_parser("next-runs", help="print the next scheduled run times")
    sub.add_parser("print-prompt", help="print the prompt that would be sent to the agent")
    sub.add_parser("print-schema", help="print the JSON schema required from the agent")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        settings = load_settings()
    except Exception as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    setup_logging(settings.log_level, settings.secret_values())

    if args.command == "next-runs":
        from .scheduler import next_run_times, systemd_on_calendar

        for t in next_run_times(dt.datetime.now(dt.timezone.utc), 4):
            print(t.isoformat())
        print("\n".join(systemd_on_calendar()))
        return EXIT_OK

    if args.command == "print-schema":
        from .schemas import codex_output_schema

        print(json.dumps(codex_output_schema(), indent=2))
        return EXIT_OK

    try:
        from .services import build_services

        services = build_services(settings, args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_CONFIG

    if args.command == "check-config":
        cfg = services.cfg
        print(f"config: {cfg.name} (hash {cfg.config_hash})")
        print(f"criteria: {len(cfg.criteria)}, statuses: {', '.join(cfg.statuses)}")
        print(f"agent backend: {settings.agent_backend}")
        print(f"database: {settings.resolved_database_url}")
        print(f"telegram configured: {bool(settings.telegram_bot_token and settings.telegram_allowed_user_id)}")
        return EXIT_OK

    if args.command == "init-db":
        from .db.session import upgrade_database

        upgrade_database(settings.resolved_database_url)
        log.info("database is at the latest schema revision")
        return EXIT_OK

    if args.command == "print-prompt":
        from .db import repository as repo
        from .db.session import session_scope
        from .prompt import build_prompt

        with session_scope(services.session_factory) as s:
            print(build_prompt(services.cfg, repo.records_for_prompt(s, services.cfg.limits.existing_records_in_prompt)))
        return EXIT_OK

    if args.command == "run":
        from .services import make_runner

        summary = make_runner(services, backend=args.agent, notify=not args.no_notify).run(args.trigger)
        print(summary.as_text())
        if summary.status == "skipped":
            return EXIT_SKIPPED
        return EXIT_FAILED if summary.status == "failed" else EXIT_OK

    if args.command == "bot":
        from .bot.app import run_bot

        run_bot(services)
        return EXIT_OK

    if args.command == "dev-scheduler":
        from .scheduler import run_dev_scheduler
        from .services import make_runner

        run_dev_scheduler(lambda: make_runner(services, backend=args.agent).run("timer"))
        return EXIT_OK

    return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
