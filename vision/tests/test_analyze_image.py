"""Tests for server.tools.analyze_image — analyze_image MCP tool."""

import json
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.mimo import MimoVisionProvider
from server.tools.analyze_image import register as register_tool


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stub that satisfies the ImageProvider protocol."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name

    def understand(self, image_source: str, prompt: str = "请详细描述这张图片的内容", **kwargs) -> str:
        return json.dumps({
            "image_url": image_source,
            "prompt": prompt,
            "understanding": "这是一张示例图片",
            "model": "stub-model",
        })


@pytest.fixture(autouse=True)
def _register_stub() -> None:
    """Register a stub provider for tests that need one."""
    register("stub", _StubProvider())
    yield


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_register_creates_tool() -> None:
    """register_tool adds an 'analyze_image' tool to the MCP server."""
    mcp = MagicMock()
    register_tool(mcp, default_provider="stub")

    mcp.tool.assert_called_once()
    inner_decorator = mcp.tool.return_value
    inner_decorator.assert_called_once()
    func = inner_decorator.call_args[0][0]
    assert func.__name__ == "analyze_image"


class _MockMCP:
    """A minimal stand-in for FastMCP that captures the tool decorator."""

    def __init__(self) -> None:
        self.captured_tools: dict[str, dict] = {}

    def tool(self, **kwargs: object) -> Callable:
        tool_meta = kwargs

        def decorator(func: Callable) -> Callable:
            self.captured_tools[func.__name__] = {
                "function": func,
                "description": tool_meta.get("description", ""),
            }
            return func

        return decorator


def test_tool_description_contains_image() -> None:
    """The tool description mentions image and analyze."""
    mcp = _MockMCP()
    register_tool(mcp)
    desc = mcp.captured_tools["analyze_image"]["description"].lower()
    assert "image" in desc
    assert "analyze" in desc


# ---------------------------------------------------------------------------
# Tool invocation — analyze_image function
# ---------------------------------------------------------------------------


def _get_analyze_image_func() -> Callable:
    """Helper to extract the analyze_image function."""
    return _get_analyze_image_func_from_registration("stub")


def _get_analyze_image_func_from_registration(default_provider: str) -> Callable:
    """Register the tool on a mock MCP and return the inner function."""
    mcp = MagicMock(spec=["tool"])
    inner_decorator = MagicMock()

    def capture_tool(**kwargs):
        return inner_decorator

    mcp.tool = capture_tool

    register_tool(mcp, default_provider=default_provider)
    func = inner_decorator.call_args[0][0]
    return func


def test_analyze_image_calls_provider() -> None:
    """analyze_image calls get_provider and returns results."""
    func = _get_analyze_image_func()

    with patch("server.tools.analyze_image.get_provider") as mock_get:
        mock_get.return_value = _StubProvider()
        result = func("https://example.com/img.jpg")
        data = json.loads(result)

    mock_get.assert_called_once_with("stub")
    assert data["image_url"] == "https://example.com/img.jpg"
    assert "示例图片" in data["understanding"]


def test_analyze_image_default_provider() -> None:
    """analyze_image uses default_provider when no provider argument is given."""
    register("custom_stub", _StubProvider("custom_stub"))
    func = _get_analyze_image_func_from_registration("custom_stub")

    with patch("server.tools.analyze_image.get_provider") as mock_get:
        mock_get.return_value = _StubProvider("custom_stub")
        result = func("https://example.com/img.jpg")
        json.loads(result)  # just verify it parses

    mock_get.assert_called_once_with("custom_stub")


def test_analyze_image_custom_provider_arg() -> None:
    """analyze_image accepts a provider argument overriding the default."""
    register("custom_stub", _StubProvider("custom_stub"))
    func = _get_analyze_image_func_from_registration("stub")

    with patch("server.tools.analyze_image.get_provider") as mock_get:
        mock_get.return_value = _StubProvider("custom_stub")
        func("https://example.com/img.jpg", provider="custom_stub")

    mock_get.assert_called_once_with("custom_stub")


def test_analyze_image_custom_prompt() -> None:
    """analyze_image forwards the prompt to the provider."""
    stub = _StubProvider()
    func = _get_analyze_image_func()

    with patch("server.tools.analyze_image.get_provider", return_value=stub) as mock_get:
        with patch.object(stub, "understand", wraps=stub.understand) as mock_understand:
            func("https://example.com/img.jpg", prompt="图中有什么？")

    mock_get.assert_called_once_with("stub")
    mock_understand.assert_called_once_with(
        "https://example.com/img.jpg",
        prompt="图中有什么？",
    )


def test_analyze_image_unknown_provider() -> None:
    """analyze_image returns JSON error for an unknown provider."""
    func = _get_analyze_image_func()
    result = func("https://example.com/img.jpg", provider="nonexistent")
    data = json.loads(result)
    assert data["error"].startswith("Unknown image provider")
    assert data["image_url"] == "https://example.com/img.jpg"
    assert data["understanding"] == ""


# ---------------------------------------------------------------------------
# Integration with real provider
# ---------------------------------------------------------------------------


def test_real_mimo_integration_via_tool(mimo_api_response: dict) -> None:
    """analyze_image tool works with the real MimoVisionProvider under mocking."""
    register("mimo", MimoVisionProvider())

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            func = _get_analyze_image_func_from_registration("mimo")
            result = func("https://example.com/sunset.jpg", provider="mimo")
            data = json.loads(result)

    assert data["image_url"] == "https://example.com/sunset.jpg"
    assert "日落" in data["understanding"]
    assert data["model"] == "mimo-v2.5"
