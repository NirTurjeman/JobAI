import pytest

from webtracker.config import ConfigError, Settings, load_research_config, load_settings


def test_example_config_loads(cfg):
    assert cfg.name == "example-topic-tracker"
    assert "new" in cfg.statuses and "dismissed" in cfg.statuses
    assert cfg.criteria[0].include_keywords == ["example topic"]
    assert len(cfg.config_hash) == 16


@pytest.mark.parametrize(
    "yaml_text, fragment",
    [
        ("name: x\nobjective: y\nunknown_key: 1\n", "unknown_key"),
        ("name: x\nobjective: y\nstatuses: [new, saved]\n", "dismissed"),
        ("name: x\nobjective: y\nstatuses: [new, dismissed, 'Bad Status']\n", "invalid status"),
        ("name: x\nobjective: y\nscoring: {notify_min_score: 2}\n", "notify_min_score"),
        ("name: x\nobjective: y\ncriteria: [{name: a, description: d}, {name: a, description: d}]\n", "unique"),
        ("- just\n- a list\n", "mapping"),
    ],
)
def test_invalid_configs_rejected(tmp_path, yaml_text, fragment):
    p = tmp_path / "c.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ConfigError) as ei:
        load_research_config(p)
    assert fragment in str(ei.value)


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError):
        load_research_config(tmp_path / "nope.yaml")


def test_settings_from_env_hide_secret(monkeypatch):
    monkeypatch.setenv("WT_TELEGRAM_BOT_TOKEN", "123456:" + "A" * 35)
    monkeypatch.setenv("WT_TELEGRAM_ALLOWED_USER_ID", "42")
    s = load_settings()
    assert s.telegram_allowed_user_id == 42
    assert "AAAA" not in repr(s)
    assert s.secret_values() == ["123456:" + "A" * 35]
    assert s.notification_chat_id == 42


def test_settings_from_systemd_credentials(monkeypatch, tmp_path):
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "WT_TELEGRAM_BOT_TOKEN").write_text("999:secretvalue")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    s = load_settings()
    assert s.telegram_bot_token.get_secret_value() == "999:secretvalue"


def test_empty_env_values_ignored(monkeypatch):
    monkeypatch.setenv("WT_TELEGRAM_ALLOWED_USER_ID", "")
    assert load_settings().telegram_allowed_user_id is None


def test_default_paths():
    s = Settings(_env_file=None)
    assert s.resolved_database_url.endswith("data/webtracker.sqlite3")
    assert str(s.resolved_lock_file).endswith("research.lock")
