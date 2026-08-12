from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.config import WebConfig
from server.main import _build_parser, _config_from_args, load_environment, main


def test_config_reads_tavily_and_http_settings() -> None:
    with patch.dict(
        "os.environ",
        {
            "TAVILY_API_KEY": "tvly-test",
            "TAVILY_SEARCH_DEPTH": "advanced",
            "TAVILY_TOPIC": "news",
            "ARCHON_WEB_TIMEOUT": "20",
            "ARCHON_WEB_TRANSPORT": "streamable-http",
            "ARCHON_WEB_HOST": "0.0.0.0",
            "ARCHON_WEB_PORT": "9000",
            "ARCHON_WEB_STATELESS_HTTP": "true",
        },
        clear=True,
    ):
        config = WebConfig.from_env()
    assert config.tavily_api_key == "tvly-test"
    assert config.tavily_search_depth == "advanced"
    assert config.tavily_topic == "news"
    assert config.timeout == 20
    assert config.transport == "streamable-http"
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.stateless_http is True


def test_config_rejects_invalid_tavily_options_and_clamps_http_values() -> None:
    with patch.dict(
        "os.environ",
        {"TAVILY_SEARCH_DEPTH": "deep", "TAVILY_TOPIC": "images", "ARCHON_WEB_PORT": "99999"},
        clear=True,
    ):
        config = WebConfig.from_env()
    assert config.tavily_search_depth == "basic"
    assert config.tavily_topic == "general"
    assert config.port == 65535


def test_load_environment_uses_project_file_first() -> None:
    expected = Path("/project/.env")
    with patch("server.main.Path.exists", side_effect=[True]):
        with patch("server.main.Path.resolve", return_value=Path("/project/server/main.py")):
            with patch("server.main.load_dotenv") as load:
                assert load_environment() == expected
    load.assert_called_once_with(expected, override=False)


def test_cli_rejects_invalid_port_and_forwards_http_options() -> None:
    args = _build_parser().parse_args(["--port", "0"])
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _config_from_args(args, WebConfig())

    server = MagicMock()
    with patch("server.main.WebConfig.from_env", return_value=WebConfig()):
        with patch("server.main.create_server", return_value=server):
            main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    server.run.assert_called_once_with(
        transport="streamable-http", host="0.0.0.0", port=9000, streamable_http_path="/mcp", stateless_http=False
    )
