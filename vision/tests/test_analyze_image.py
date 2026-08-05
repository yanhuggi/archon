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


def test_analyze_image_does_not_expose_provider_argument() -> None:
    """Provider routing remains an internal server concern."""
    import inspect

    func = _get_analyze_image_func_from_registration("stub")
    assert "provider" not in inspect.signature(func).parameters


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
    func = _get_analyze_image_func_from_registration("nonexistent")
    result = func("https://example.com/img.jpg")
    data = json.loads(result)
    assert data["error"].startswith("Unknown image provider")
    assert data["error_code"] == "provider_unavailable"
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
            result = func("https://example.com/sunset.jpg")
            data = json.loads(result)

    assert data["image_url"] == "https://example.com/sunset.jpg"
    assert "日落" in data["understanding"]
    assert data["model"] == "mimo-v2.5"


def test_analyze_image_rejects_empty_prompt() -> None:
    func = _get_analyze_image_func()
    data = json.loads(func("https://example.com/img.jpg", prompt="   "))
    assert data["error_code"] == "invalid_prompt"


def test_analyze_image_handles_invalid_provider_json() -> None:
    func = _get_analyze_image_func()
    provider = MagicMock()
    provider.understand.return_value = "not-json"
    with patch("server.tools.analyze_image.get_provider", return_value=provider):
        data = json.loads(func("https://example.com/img.jpg"))
    assert data["error_code"] == "invalid_provider_response"


# ---------------------------------------------------------------------------
# Response envelope shape
# ---------------------------------------------------------------------------

_ENVELOPE_KEYS = {"image_url", "prompt", "understanding", "model"}
_ERROR_KEYS = _ENVELOPE_KEYS | {"error", "error_code"}


def test_tool_layer_errors_match_the_documented_envelope() -> None:
    """Every tool-layer failure carries the same keys as a provider failure."""
    cases = [
        (_get_analyze_image_func(), ("https://example.com/img.jpg", "   "), "invalid_prompt"),
        (_get_analyze_image_func(), ("   ", "q"), "invalid_image_source"),
        (
            _get_analyze_image_func_from_registration("nonexistent"),
            ("https://example.com/img.jpg", "q"),
            "provider_unavailable",
        ),
    ]
    for func, args, expected_code in cases:
        data = json.loads(func(*args))
        assert data["error_code"] == expected_code
        assert set(data) == _ERROR_KEYS, f"{expected_code} envelope: {sorted(data)}"
        assert data["understanding"] == ""


def test_tool_layer_error_reports_configured_model() -> None:
    """The error envelope's model field reflects configuration, not a guess."""
    func = _get_analyze_image_func_from_registration("nonexistent")
    with patch.dict("os.environ", {"MIMO_MODEL": "mimo-v9"}):
        data = json.loads(func("https://example.com/img.jpg"))
    assert data["model"] == "mimo-v9"


def test_provider_error_envelope_is_complete() -> None:
    """An exception inside the provider still yields the full envelope."""
    func = _get_analyze_image_func()
    provider = MagicMock()
    provider.understand.side_effect = RuntimeError("boom")
    with patch("server.tools.analyze_image.get_provider", return_value=provider):
        data = json.loads(func("https://example.com/img.jpg"))
    assert data["error_code"] == "provider_error"
    assert set(data) == _ERROR_KEYS


def test_partial_provider_response_is_backfilled() -> None:
    """A provider reply missing contract keys is completed, not passed through."""
    func = _get_analyze_image_func()
    provider = MagicMock()
    provider.understand.return_value = json.dumps({"understanding": "看到一只猫"})
    with patch("server.tools.analyze_image.get_provider", return_value=provider):
        data = json.loads(func("https://example.com/img.jpg", prompt="有什么动物"))
    assert set(data) == _ENVELOPE_KEYS
    assert data["understanding"] == "看到一只猫"
    assert data["prompt"] == "有什么动物"
