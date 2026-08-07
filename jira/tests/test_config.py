"""Tests for Jira runtime configuration."""

from pathlib import Path

from server.config import JiraConfig


def test_config_reads_provider_server_and_output_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com/context/")
    monkeypatch.setenv("JIRA_USERNAME", " user ")
    monkeypatch.setenv("JIRA_PASSWORD", "password")
    monkeypatch.setenv("JIRA_TIMEOUT", "45")
    monkeypatch.setenv("JIRA_MAX_ATTACHMENT_SIZE", "12345")
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("JIRA_ALLOW_OVERWRITE", "true")
    monkeypatch.setenv("JIRA_JQL_DISK_CACHE", "false")
    monkeypatch.setenv("JIRA_JQL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("JIRA_JQL_FIELD_REFRESH_INTERVAL", "120")
    monkeypatch.setenv("JIRA_JQL_VALUE_REFRESH_INTERVAL", "240")
    monkeypatch.setenv("JIRA_JQL_CACHE_MAX_STALE", "3600")
    monkeypatch.setenv("JIRA_JQL_VALUE_CACHE_MAX_ENTRIES", "25")
    monkeypatch.setenv("JIRA_RETRY_MUTATIONS_ON_401", "true")
    monkeypatch.setenv("ARCHON_JIRA_TRANSPORT", "streamable-http")
    config = JiraConfig.from_env()
    assert config.url == "https://jira.example.com/context"
    assert config.username == "user"
    assert config.password == "password"
    assert config.timeout == 45
    assert config.max_attachment_size == 12345
    assert config.output_dir == tmp_path.resolve()
    assert config.allow_overwrite is True
    assert config.jql_disk_cache_enabled is False
    assert config.jql_cache_dir == (tmp_path / "cache").resolve()
    assert config.jql_field_refresh_interval == 120
    assert config.jql_value_refresh_interval == 240
    assert config.jql_cache_max_stale == 3600
    assert config.jql_value_cache_max_entries == 25
    assert config.retry_mutations_on_401 is True
    assert config.transport == "streamable-http"
    assert config.is_configured


def test_mutation_retry_is_disabled_by_default(monkeypatch) -> None:
    """Repeating a write after a 401 must be an explicit deployment decision."""
    monkeypatch.delenv("JIRA_RETRY_MUTATIONS_ON_401", raising=False)
    assert JiraConfig.from_env().retry_mutations_on_401 is False
    assert JiraConfig().retry_mutations_on_401 is False


def test_invalid_url_and_values_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("JIRA_URL", "not-a-url")
    monkeypatch.setenv("JIRA_TIMEOUT", "nan")
    monkeypatch.setenv("ARCHON_JIRA_TRANSPORT", "invalid")
    config = JiraConfig.from_env()
    assert config.url is None
    assert config.timeout == 30
    assert config.transport == "stdio"
    assert not config.is_configured


def test_default_output_dir_is_working_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JIRA_ALLOWED_OUTPUT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert JiraConfig.from_env().output_dir == tmp_path.resolve()
