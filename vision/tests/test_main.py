"""Tests for the archon-vision MCP server entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.config import VisionConfig
from server.main import SERVER_NAME, _build_parser, _config_from_args, create_server, load_environment, main


def test_load_environment_uses_first_file_without_override() -> None:
    with patch("server.main.Path.exists", side_effect=[False, True]):
        with patch("server.main.load_dotenv") as mock_load:
            loaded = load_environment()
    assert isinstance(loaded, Path)
    mock_load.assert_called_once_with(loaded, override=False)


def test_load_environment_returns_none_when_missing() -> None:
    with patch("server.main.Path.exists", return_value=False):
        with patch("server.main.load_dotenv") as mock_load:
            assert load_environment() is None
    mock_load.assert_not_called()


def test_create_server_always_exposes_one_tool_and_instructions() -> None:
    with patch("server.main.atexit.register"):
        server = create_server(VisionConfig(api_key=None))
    tools = asyncio.run(server.list_tools())
    prompts = asyncio.run(server.list_prompts())
    assert [tool.name for tool in tools] == ["analyze_image"]
    assert "provider" not in tools[0].input_schema["properties"]
    assert prompts == []
    assert server._lowlevel_server.name == SERVER_NAME
    assert "depends on pixels" in server._lowlevel_server.instructions


def test_main_runs_stdio_by_default() -> None:
    fake_server = MagicMock()
    with patch("server.main.VisionConfig.from_env", return_value=VisionConfig(api_key="key")):
        with patch("server.main.create_server", return_value=fake_server):
            main([])
    fake_server.run.assert_called_once_with(transport="stdio")


def test_main_runs_streamable_http_with_cli_overrides() -> None:
    fake_server = MagicMock()
    with patch("server.main.VisionConfig.from_env", return_value=VisionConfig(api_key="key")):
        with patch("server.main.create_server", return_value=fake_server):
            main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    fake_server.run.assert_called_once_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=9000,
        streamable_http_path="/mcp",
        stateless_http=False,
        max_request_body_size=VisionConfig().max_request_body_size,
    )


def test_streamable_http_body_limit_fits_a_maximum_image() -> None:
    """The HTTP body limit must accept a base64 image at max_image_size."""
    fake_server = MagicMock()
    config = VisionConfig(api_key="key", max_image_size=8 * 1024 * 1024)
    with patch("server.main.VisionConfig.from_env", return_value=config):
        with patch("server.main.create_server", return_value=fake_server):
            main(["--transport", "streamable-http"])

    limit = fake_server.run.call_args.kwargs["max_request_body_size"]
    base64_size = ((config.max_image_size + 2) // 3) * 4
    assert limit > base64_size, "body limit must leave room for base64 inflation"
    assert limit > 4 * 1024 * 1024, "must exceed the SDK's 4 MiB default"


def test_main_builds_exactly_one_server() -> None:
    """Startup must not build a second, unused server via module import."""
    import server.main as module

    with patch.object(module, "create_server", return_value=MagicMock()) as mock_create:
        with patch("server.main.VisionConfig.from_env", return_value=VisionConfig(api_key="key")):
            main([])
    assert mock_create.call_count == 1


def test_module_level_mcp_is_lazy_and_cached() -> None:
    """The `mcp` CLI entry point resolves on demand, then memoizes."""
    import server.main as module

    globals_dict = vars(module)
    had_mcp = "mcp" in globals_dict
    previous = globals_dict.get("mcp")
    globals_dict.pop("mcp", None)
    try:
        sentinel = MagicMock()
        with patch.object(module, "create_server", return_value=sentinel) as mock_create:
            first = module.mcp
            second = module.mcp
        assert first is sentinel and second is sentinel
        assert mock_create.call_count == 1
    finally:
        globals_dict.pop("mcp", None)
        if had_mcp:
            globals_dict["mcp"] = previous


def test_module_getattr_still_rejects_unknown_names() -> None:
    import server.main as module

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        module.nope


def test_cli_rejects_invalid_port() -> None:
    args = _build_parser().parse_args(["--port", "0"])
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _config_from_args(args, VisionConfig())
