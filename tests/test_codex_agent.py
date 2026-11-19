import json
import os
import stat
import sys
import textwrap

import pytest

from webtracker.agents.base import AgentExecutionError, ResearchTask
from webtracker.agents.codex import CodexAgent, extract_json_text
from webtracker.agents.factory import create_agent
from webtracker.agents.mock import MockAgent
from webtracker.config import BrowserConfig
from webtracker.notify.base import MemoryNotifier
from webtracker.runner import ResearchRunner
from webtracker.schemas import codex_output_schema

from conftest import make_record

GOOD_OUTPUT = {"records": [make_record(title="Example topic from codex")], "sources_consulted": [], "notes": None}


def fake_codex(tmp_path, body):
    """Create an executable fake `codex` that records its argv/env/stdin to calls.json."""
    script = tmp_path / "fake_codex"
    log = tmp_path / "calls.json"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""
            import json, os, sys, time
            args = sys.argv[1:]
            prompt = sys.stdin.read()
            json.dump({{"args": args, "env": dict(os.environ), "prompt": prompt, "cwd": os.getcwd()}},
                      open({str(log)!r}, "w"))
            out = args[args.index("--output-last-message") + 1]
            """
        )
        + textwrap.dedent(body)
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def task(cfg):
    return ResearchTask(prompt="PROMPT-TEXT", config=cfg, output_schema=codex_output_schema(), run_id=1)


def test_command_is_sandboxed_and_safe(tmp_path):
    agent = CodexAgent(binary="codex", model="some-model")
    cmd = agent.build_command(tmp_path, tmp_path / "s.json", tmp_path / "o.json")
    assert cmd[:2] == ["codex", "exec"]
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert 'web_search="live"' in cmd
    assert "--ephemeral" in cmd and "--skip-git-repo-check" in cmd
    assert cmd[-1] == "-"  # prompt via stdin, never on the command line
    joined = " ".join(cmd)
    for bad in ("dangerously", "danger-full-access", "--full-auto", "workspace-write"):
        assert bad not in joined
    assert "mcp_servers" not in joined


def test_browser_mcp_option(tmp_path):
    agent = CodexAgent(browser=BrowserConfig(enabled=True, mcp_command="npx", mcp_args=["@playwright/mcp", "--headless"]))
    cmd = agent.build_command(tmp_path, tmp_path / "s", tmp_path / "o")
    assert 'mcp_servers.browser.command="npx"' in cmd
    assert 'mcp_servers.browser.args=["@playwright/mcp", "--headless"]' in cmd


def test_forbidden_option_refused(tmp_path):
    agent = CodexAgent(browser=BrowserConfig(enabled=True, mcp_args=["--dangerously-bypass-approvals-and-sandbox"]))
    with pytest.raises(AgentExecutionError):
        agent.build_command(tmp_path, tmp_path / "s", tmp_path / "o")


def test_env_allowlist_excludes_app_secrets(monkeypatch):
    monkeypatch.setenv("WT_TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    monkeypatch.setenv("CODEX_HOME", "/tmp/codexhome")
    env = CodexAgent().build_env()
    assert "WT_TELEGRAM_BOT_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["CODEX_HOME"] == "/tmp/codexhome" and "PATH" in env


def test_successful_run_with_fake_binary(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("WT_TELEGRAM_BOT_TOKEN", "123:secret")
    script, log = fake_codex(
        tmp_path, f"open(out, 'w').write('```json\\n' + {json.dumps(json.dumps(GOOD_OUTPUT))} + '\\n```')\n"
    )
    resp = CodexAgent(binary=str(script), timeout_seconds=30).research(task(cfg))
    assert json.loads(resp.raw_output) == GOOD_OUTPUT
    call = json.loads(log.read_text())
    assert call["prompt"] == "PROMPT-TEXT"
    assert "WT_TELEGRAM_BOT_TOKEN" not in call["env"]
    assert os.path.basename(call["cwd"]) == "work" and not os.path.exists(call["cwd"])  # empty temp dir, removed
    schema_path = call["args"][call["args"].index("--output-schema") + 1]
    assert not os.path.exists(schema_path)  # temp dir cleaned up


def test_nonzero_exit(tmp_path, cfg):
    script, _ = fake_codex(tmp_path, "sys.stderr.write('auth error'); sys.exit(2)\n")
    with pytest.raises(AgentExecutionError) as ei:
        CodexAgent(binary=str(script), timeout_seconds=30).research(task(cfg))
    assert "status 2" in str(ei.value) and "auth error" in ei.value.excerpt


def test_auth_failure_hint(tmp_path, cfg):
    script, _ = fake_codex(tmp_path, "sys.stderr.write('Please log out and sign in again.'); sys.exit(1)\n")
    with pytest.raises(AgentExecutionError, match="codex login"):
        CodexAgent(binary=str(script), timeout_seconds=30).research(task(cfg))


def test_missing_output(tmp_path, cfg):
    script, _ = fake_codex(tmp_path, "pass\n")
    with pytest.raises(AgentExecutionError, match="no final message"):
        CodexAgent(binary=str(script), timeout_seconds=30).research(task(cfg))


def test_timeout_kills_process(tmp_path, cfg):
    script, _ = fake_codex(tmp_path, "time.sleep(60)\n")
    agent = CodexAgent(binary=str(script))
    agent.timeout_seconds = 1
    with pytest.raises(AgentExecutionError, match="timed out"):
        agent.research(task(cfg))


def test_missing_binary(cfg):
    with pytest.raises(AgentExecutionError, match="not found"):
        CodexAgent(binary="/nonexistent/codex").research(task(cfg))


def test_extract_json_text():
    assert extract_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json_text('Here you go: {"a": 1} thanks') == '{"a": 1}'
    assert extract_json_text('{"a": 1}') == '{"a": 1}'


def test_runner_with_fake_codex_end_to_end(tmp_path, cfg, session_factory):
    script, _ = fake_codex(tmp_path, f"open(out, 'w').write({json.dumps(json.dumps(GOOD_OUTPUT))})\n")
    n = MemoryNotifier()
    s = ResearchRunner(cfg, session_factory, CodexAgent(binary=str(script), timeout_seconds=30), n).run()
    assert s.status == "succeeded" and s.new == 1 and len(n.sent) == 1


def test_factory(settings, cfg):
    assert isinstance(create_agent(settings, cfg, "mock"), MockAgent)
    assert isinstance(create_agent(settings, cfg, "codex"), CodexAgent)
    with pytest.raises(ValueError):
        create_agent(settings, cfg, "other")
