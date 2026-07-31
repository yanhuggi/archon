"""Tests for the archon-web MCP server entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.config import WebConfig
from server.main import SERVER_NAME, _build_parser, _config_from_args, create_server, load_environment, main


def test_load_environment_uses_first_existing_file_without_override() -> None:
    with patch("server.main.Path.exists", side_effect=[False, True]):
        with patch("server.main.load_dotenv") as mock_load:
            loaded = load_environment()

    assert isinstance(loaded, Path)
    mock_load.assert_called_once_with(loaded, override=False)


def test_load_environment_returns_none_when_no_file_exists() -> None:
    with patch("server.main.Path.exists", return_value=False):
        with patch("server.main.load_dotenv") as mock_load:
            assert load_environment() is None
    mock_load.assert_not_called()


def test_create_server_exposes_only_search_tool_and_metadata() -> None:
    server = create_server(WebConfig())
    tools = asyncio.run(server.list_tools())
    prompts = asyncio.run(server.list_prompts())

    assert [tool.name for tool in tools] == ["web_search"]
    assert prompts == []
    assert server._lowlevel_server.name == SERVER_NAME
    assert "source URLs" in server._lowlevel_server.instructions


def test_main_runs_stdio_by_default() -> None:
    fake_server = MagicMock()
    with patch("server.main.WebConfig.from_env", return_value=WebConfig()):
        with patch("server.main.create_server", return_value=fake_server):
            main([])
    fake_server.run.assert_called_once_with(transport="stdio")


def test_main_runs_streamable_http_with_cli_overrides() -> None:
    fake_server = MagicMock()
    with patch("server.main.WebConfig.from_env", return_value=WebConfig()):
        with patch("server.main.create_server", return_value=fake_server):
            main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])

    fake_server.run.assert_called_once_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=9000,
        streamable_http_path="/mcp",
        stateless_http=False,
    )


def test_cli_rejects_zero_port_instead_of_ignoring_it() -> None:
    args = _build_parser().parse_args(["--port", "0"])
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _config_from_args(args, WebConfig())
