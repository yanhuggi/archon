"""Tests for the archon-jira MCP server entry point."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.config import JiraConfig
from server.main import (
    SERVER_NAME,
    _build_parser,
    _config_from_args,
    create_server,
    load_environment,
    main,
)


def test_load_environment_uses_first_file_without_override() -> None:
    with (
        patch("server.main.Path.exists", side_effect=[False, True]),
        patch("server.main.load_dotenv") as mock_load,
    ):
        loaded = load_environment()
    assert isinstance(loaded, Path)
    mock_load.assert_called_once_with(loaded, override=False)


def test_create_server_always_exposes_stable_tools() -> None:
    with patch("server.main.atexit.register"):
        server = create_server(JiraConfig())
    tools = asyncio.run(server.list_tools())
    prompts = asyncio.run(server.list_prompts())
    assert [tool.name for tool in tools] == [
        "search_jql_fields",
        "get_jql_value_suggestions",
        "search_issues",
        "get_issue",
        "get_comments",
        "get_attachment",
        "export_issue",
    ]
    assert all("provider" not in tool.input_schema["properties"] for tool in tools)
    assert prompts == []
    assert server._lowlevel_server.name == SERVER_NAME


def test_main_runs_stdio_by_default() -> None:
    fake_server = MagicMock()
    with (
        patch("server.main.JiraConfig.from_env", return_value=JiraConfig()),
        patch("server.main.create_server", return_value=fake_server),
    ):
        main([])
    fake_server.run.assert_called_once_with(transport="stdio")


def test_main_runs_streamable_http_with_overrides() -> None:
    fake_server = MagicMock()
    with (
        patch("server.main.JiraConfig.from_env", return_value=JiraConfig()),
        patch("server.main.create_server", return_value=fake_server),
    ):
        main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    fake_server.run.assert_called_once_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=9000,
        streamable_http_path="/mcp",
        stateless_http=False,
    )


def test_cli_rejects_invalid_port() -> None:
    args = _build_parser().parse_args(["--port", "0"])
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _config_from_args(args, JiraConfig())
