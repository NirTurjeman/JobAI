import json

from webtracker.cli import EXIT_CONFIG, EXIT_OK, EXIT_SKIPPED, main
from webtracker.locking import RunLock

from conftest import EXAMPLE_CONFIG, MOCK_FIXTURE


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WT_RESEARCH_CONFIG", str(EXAMPLE_CONFIG))
    monkeypatch.setenv("WT_MOCK_FIXTURE", str(MOCK_FIXTURE))


def test_cli_mock_flow(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    assert main(["check-config"]) == EXIT_OK
    assert main(["init-db"]) == EXIT_OK
    assert main(["run", "--agent", "mock"]) == EXIT_OK
    assert "3 new" in capsys.readouterr().out
    assert main(["run", "--agent", "mock", "--no-notify"]) == EXIT_OK
    assert "0 new, 3 updated" in capsys.readouterr().out
    assert main(["print-prompt"]) == EXIT_OK
    assert "UNTRUSTED-DATA-" in capsys.readouterr().out


def test_cli_run_skipped_when_locked(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    main(["init-db"])
    with RunLock(tmp_path / "data" / "research.lock"):
        assert main(["run", "--agent", "mock"]) == EXIT_SKIPPED


def test_cli_bad_config(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    assert main(["--config", str(bad), "check-config"]) == EXIT_CONFIG


def test_cli_print_schema_and_next_runs(capsys):
    assert main(["print-schema"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["additionalProperties"] is False
    assert main(["next-runs"]) == EXIT_OK
    assert "Asia/Jerusalem" in capsys.readouterr().out
