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


def test_two_servers_do_not_share_one_provider_config() -> None:
    """The provider registry is process-global; each server must stay isolated.

    Before this was fixed, creating a second server overwrote "duckduckgo", so
    the first server's searches used the second server's timeout and proxy.
    """
    import server.main as main_module

    server_a = create_server(WebConfig(interval=0.0, timeout=11, proxy="http://proxy-a:1"))
    server_b = create_server(WebConfig(interval=0.0, timeout=22, proxy="http://proxy-b:2"))

    seen: list[dict[str, object]] = []

    class _RecordingDDGS:
        def __init__(self, **kwargs: object) -> None:
            seen.append(kwargs)

        def __enter__(self) -> _RecordingDDGS:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def text(self, query: str, **kwargs: object) -> list[dict[str, str]]:
            return []

    with patch("server.providers.duckduckgo.DDGS", _RecordingDDGS):
        asyncio.run(server_a.call_tool("web_search", {"query": "x"}))
        asyncio.run(server_b.call_tool("web_search", {"query": "x"}))

    assert seen[0] == {"timeout": 11, "proxy": "http://proxy-a:1"}
    assert seen[1] == {"timeout": 22, "proxy": "http://proxy-b:2"}
    assert main_module is not None


def test_module_level_mcp_is_built_lazily() -> None:
    """Eager construction made every startup build a second unused server."""
    import server.main as main_module

    main_module.__dict__.pop("mcp", None)
    with patch.object(main_module, "create_server", wraps=main_module.create_server) as spy:
        assert "mcp" not in main_module.__dict__
        first = main_module.mcp
        second = main_module.mcp

    assert first is second
    spy.assert_called_once()


def test_module_getattr_still_rejects_unknown_names() -> None:
    import server.main as main_module

    missing = "does_not_exist"
    with pytest.raises(AttributeError, match="has no attribute"):
        getattr(main_module, missing)
