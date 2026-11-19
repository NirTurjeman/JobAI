"""OpenAI Codex CLI backend (``codex exec``).

Safety properties:

* Codex always runs with ``--sandbox read-only`` inside an empty, throw-away
  working directory; dangerous bypass flags are never passed.
* The process environment is an allowlist — application secrets such as the
  Telegram bot token are never visible to Codex.
* The prompt is passed on stdin and the command is an argument list (no
  shell), so nothing in the prompt or output can alter the command line.
* Output is only read back as JSON text for validation; it is never executed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ..config import BrowserConfig
from .base import AgentExecutionError, AgentResponse, ResearchAgent, ResearchTask

log = logging.getLogger(__name__)

# Environment variables Codex may need (paths, locale, proxies, its own auth).
ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    "CODEX_HOME", "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
)

FORBIDDEN_FLAGS = ("--dangerously-bypass-approvals-and-sandbox", "danger-full-access", "--full-auto", "--yolo")

_AUTH_ERROR_RE = re.compile(r"sign in again|not logged in|unauthorized \(401\)|codex login", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string literal is a valid TOML basic string


class CodexAgent(ResearchAgent):
    name = "codex"

    def __init__(
        self,
        *,
        binary: str = "codex",
        model: Optional[str] = None,
        profile: Optional[str] = None,
        timeout_seconds: int = 900,
        codex_home: Optional[Path] = None,
        browser: Optional[BrowserConfig] = None,
        web_search: str = "live",
    ) -> None:
        self.binary = binary
        self.model = model
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self.codex_home = codex_home
        self.browser = browser or BrowserConfig()
        self.web_search = web_search

    # ----------------------------------------------------------------- command

    def build_command(self, workdir: Path, schema_path: Path, output_path: Path) -> List[str]:
        cmd = [
            self.binary, "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--cd", str(workdir),
            "--output-schema", str(schema_path),
            "--output-last-message", str(output_path),
            "-c", f"web_search={_toml_str(self.web_search)}",
            # Commands Codex runs inside its sandbox get a minimal environment.
            "-c", 'shell_environment_policy.inherit="core"',
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.profile:
            cmd += ["--profile", self.profile]
        if self.browser.enabled:
            cmd += [
                "-c", f"mcp_servers.browser.command={_toml_str(self.browser.mcp_command)}",
                "-c", "mcp_servers.browser.args=[" + ", ".join(_toml_str(a) for a in self.browser.mcp_args) + "]",
            ]
        cmd.append("-")  # read the prompt from stdin
        for flag in FORBIDDEN_FLAGS:
            if any(flag in part for part in cmd):
                raise AgentExecutionError(f"refusing to run Codex with forbidden option {flag!r}")
        return cmd

    def build_env(self) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
        if self.codex_home:
            env["CODEX_HOME"] = str(self.codex_home)
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["NO_COLOR"] = "1"
        return env

    # ----------------------------------------------------------------- execution

    def research(self, task: ResearchTask) -> AgentResponse:
        with tempfile.TemporaryDirectory(prefix="webtracker-codex-") as tmp:
            tmp_path = Path(tmp)
            workdir = tmp_path / "work"
            workdir.mkdir(mode=0o700)
            schema_path = tmp_path / "output_schema.json"
            output_path = tmp_path / "last_message.json"
            schema_path.write_text(json.dumps(task.output_schema), encoding="utf-8")

            cmd = self.build_command(workdir, schema_path, output_path)
            log.info("starting codex exec (run %s, timeout %ss)", task.run_id, self.timeout_seconds)
            stdout, stderr, code = self._run(cmd, task.prompt, workdir)

            if code != 0:
                msg = f"codex exited with status {code}"
                if _AUTH_ERROR_RE.search(stderr or ""):
                    msg += " (authentication failed; run `codex login` as the service user)"
                raise AgentExecutionError(msg, excerpt=_tail(stderr or stdout))
            if not output_path.exists():
                raise AgentExecutionError("codex produced no final message", excerpt=_tail(stderr or stdout))
            raw = output_path.read_text(encoding="utf-8", errors="replace")
            return AgentResponse(
                raw_output=extract_json_text(raw),
                backend=self.name,
                diagnostics={"stderr_tail": _tail(stderr, 1000)},
            )

    def _run(self, cmd: List[str], prompt: str, workdir: Path):
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.build_env(),
                cwd=str(workdir),
                text=True,
                start_new_session=True,  # own process group so a timeout kills children too
            )
        except FileNotFoundError as exc:
            raise AgentExecutionError(f"codex binary not found: {self.binary}") from exc
        try:
            stdout, stderr = proc.communicate(prompt, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            stdout, stderr = proc.communicate()
            raise AgentExecutionError(
                f"codex timed out after {self.timeout_seconds}s", excerpt=_tail(stderr or stdout)
            )
        return stdout, stderr, proc.returncode


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, PermissionError):
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _tail(text: Optional[str], n: int = 2000) -> str:
    return (text or "")[-n:]


def extract_json_text(raw: str) -> str:
    """Return the JSON object text from Codex's final message (tolerates code fences)."""
    raw = raw.strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    if raw.startswith("{"):
        return raw
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return raw
