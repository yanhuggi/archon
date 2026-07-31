"""Tests for environment-backed web configuration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from server.config import WebConfig


def test_config_defaults() -> None:
    config = WebConfig.from_env()
    assert config.interval == 2.0
    assert config.timeout == 10
    assert config.transport == "stdio"
    assert config.host == "127.0.0.1"
    assert config.port == 8000


def test_config_reads_search_and_http_settings() -> None:
    with patch.dict(
        "os.environ",
        {
            "ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0.5",
            "ARCHON_WEB_TIMEOUT": "20",
            "ARCHON_WEB_PROXY": "http://proxy.example:8080",
            "ARCHON_WEB_RATE_LIMIT_FILE": "/tmp/archon-web-test-rate-limit",
            "ARCHON_WEB_TRANSPORT": "streamable-http",
            "ARCHON_WEB_HOST": "0.0.0.0",
            "ARCHON_WEB_PORT": "9000",
            "ARCHON_WEB_STATELESS_HTTP": "true",
        },
        clear=True,
    ):
        config = WebConfig.from_env()

    assert config.interval == 0.5
    assert config.timeout == 20
    assert config.proxy == "http://proxy.example:8080"
    assert config.rate_limit_file == Path("/tmp/archon-web-test-rate-limit")
    assert config.transport == "streamable-http"
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.stateless_http is True


def test_config_invalid_values_fall_back_or_clamp() -> None:
    with patch.dict(
        "os.environ",
        {
            "ARCHON_WEB_DUCKDUCKGO_INTERVAL": "-4",
            "ARCHON_WEB_TIMEOUT": "not-an-int",
            "ARCHON_WEB_TRANSPORT": "socket",
            "ARCHON_WEB_PORT": "99999",
            "ARCHON_WEB_LOG_LEVEL": "verbose",
        },
        clear=True,
    ):
        config = WebConfig.from_env()

    assert config.interval == 0.0
    assert config.timeout == 10
    assert config.transport == "stdio"
    assert config.port == 65535
    assert config.log_level == "INFO"
