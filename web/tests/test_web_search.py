"""Tests for server.tools.web_search — web_search MCP tool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.deepseek import DeepSeekProvider
from server.providers.duckduckgo import DuckDuckGoProvider
from server.providers.tavily import TavilyProvider
from server.tools.web_search import register as register_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stub that satisfies the SearchProvider protocol."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        return json.dumps({
            "query": query,
            "results": [{"title": "stub", "url": "https://stub.com", "snippet": "stub"}],
            "result_count": 1,
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
    """register_tool adds a 'web_search' tool to the MCP server."""
    mcp = MagicMock()
    register_tool(mcp, default_provider="stub")

    mcp.tool.assert_called_once()
    # mcp.tool is called as a decorator factory (with keyword args),
    # returns a decorator callable; the decorator is then called with the function.
    inner_decorator = mcp.tool.return_value
    inner_decorator.assert_called_once()
    # The function should be named web_search
    func = inner_decorator.call_args[0][0]
    assert func.__name__ == "web_search"


class _MockMCP:
    """A minimal stand-in for FastMCP that captures the tool decorator."""

    def __init__(self) -> None:
        self.captured_tools: dict[str, dict] = {}

    def tool(self, **kwargs: object) -> callable:
        tool_meta = kwargs

        def decorator(func: callable) -> callable:
            self.captured_tools[func.__name__] = {
                "function": func,
                "description": tool_meta.get("description", ""),
            }
            return func

        return decorator


def test_tool_description_contains_search() -> None:
    """The tool description mentions search and web."""
    mcp = _MockMCP()
    register_tool(mcp)
    desc = mcp.captured_tools["web_search"]["description"].lower()
    assert "search" in desc
    assert "web" in desc


# ---------------------------------------------------------------------------
# Tool invocation — web_search function
# ---------------------------------------------------------------------------


def _get_web_search_func() -> callable:
    """Helper to extract the web_search function registered by register_tool."""
    return _get_web_search_func_from_registration("stub")


def _get_web_search_func_from_registration(default_provider: str) -> callable:
    """Register the tool on a mock MCP and return the inner web_search function."""
    mcp = MagicMock(spec=["tool"])
    inner_decorator = MagicMock()

    def capture_tool(**kwargs):
        return inner_decorator

    mcp.tool = capture_tool

    register_tool(mcp, default_provider=default_provider)
    func = inner_decorator.call_args[0][0]
    return func


def test_web_search_calls_provider() -> None:
    """web_search calls get_provider with the given name and returns results."""
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", wraps=None) as mock_get:
        # Make get_provider return our stub
        mock_get.return_value = _StubProvider()
        result = func("hello")
        data = json.loads(result)

    mock_get.assert_called_once_with("stub")
    assert data["query"] == "hello"
    assert data["result_count"] == 1


def test_web_search_default_provider() -> None:
    """web_search uses default_provider when no provider argument is given."""
    register("custom_stub", _StubProvider("custom_stub"))
    func = _get_web_search_func_from_registration("custom_stub")

    with patch("server.tools.web_search.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProvider("custom_stub")
        result = func("hello")
        json.loads(result)  # just verify it parses

    mock_get.assert_called_once_with("custom_stub")


def test_web_search_custom_provider_arg() -> None:
    """web_search accepts a provider argument overriding the default."""
    register("custom_stub", _StubProvider("custom_stub"))
    func = _get_web_search_func_from_registration("stub")

    with patch("server.tools.web_search.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProvider("custom_stub")
        func("hello", provider="custom_stub")

    mock_get.assert_called_once_with("custom_stub")


def test_web_search_passes_max_results() -> None:
    """web_search forwards max_results to the provider."""
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub) as mock_get:
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("q", max_results=3)

    mock_get.assert_called_once_with("stub")
    mock_search.assert_called_once_with("q", max_results=3)


def test_web_search_unknown_provider() -> None:
    """web_search returns error string for an unknown provider."""
    func = _get_web_search_func()
    result = func("test", provider="nonexistent")
    assert "Unknown provider" in result


# ---------------------------------------------------------------------------
# Integration with real providers
# ---------------------------------------------------------------------------


def test_real_tavily_integration_via_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """web_search tool works with the real TavilyProvider under mocking."""
    import os
    monkeypatch.setattr("server.providers.tavily.os.environ", {"TAVILY_API_KEY": "test-key"})
    register("tavily", TavilyProvider())

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "results": [{"title": "T", "url": "https://t.com", "content": "c"}],
        }

        func = _get_web_search_func_from_registration("tavily")
        result = func("tavily test", provider="tavily")
        data = json.loads(result)

    assert data["query"] == "tavily test"
    assert data["result_count"] == 1


def test_real_deepseek_integration_via_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """web_search tool works with the real DeepSeekProvider under mocking (OpenAI format)."""
    monkeypatch.setattr("server.providers.deepseek.os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    register("deepseek", DeepSeekProvider())

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Summary",
                        "annotations": [
                            {"type": "web_search_result", "title": "D", "url": "https://d.com"},
                        ],
                    },
                },
            ],
        }

        func = _get_web_search_func_from_registration("deepseek")
        result = func("deepseek test", provider="deepseek")
        data = json.loads(result)

    assert data["query"] == "deepseek test"
    assert data["result_count"] == 1
    assert data["summary"] == "Summary"


def test_real_duckduckgo_integration_via_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """web_search tool works with the real DuckDuckGoProvider under mocking."""
    register("ddg", DuckDuckGoProvider())

    with patch("server.providers.duckduckgo.DDGS") as mock_ddgs:
        instance = mock_ddgs.return_value.__enter__.return_value
        instance.text.return_value = [
            {"title": "D1", "href": "https://d1.com", "body": "body1"},
        ]

        with patch("server.providers.duckduckgo._rate_limit"):
            func = _get_web_search_func_from_registration("ddg")
            result = func("ddg test", provider="ddg")
            data = json.loads(result)

    assert data["query"] == "ddg test"
    assert data["result_count"] == 1
