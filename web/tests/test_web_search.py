"""Tests for the public ``web_search`` MCP tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.duckduckgo import DuckDuckGoProvider
from server.tools.web_search import register as register_tool


class _StubProvider:
    """Minimal provider used by tool-level tests."""

    def search(self, query: str, max_results: int = 10, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": query,
                "results": [
                    {"title": "stub", "url": "https://stub.example", "snippet": "stub"}
                ],
                "result_count": 1,
            }
        )


@pytest.fixture(autouse=True)
def _register_stub() -> None:
    register("duckduckgo", _StubProvider())
    yield


class _MockMCP:
    """Capture the tool decorator and its metadata."""

    def __init__(self) -> None:
        self.captured_tools: dict[str, dict[str, object]] = {}

    def tool(self, **metadata: object) -> callable:
        def decorator(func: callable) -> callable:
            name = str(metadata.get("name") or func.__name__)
            self.captured_tools[name] = {"function": func, **metadata}
            return func

        return decorator


def _get_web_search_func() -> callable:
    mcp = _MockMCP()
    register_tool(mcp)
    return mcp.captured_tools["web_search"]["function"]


def test_register_creates_read_only_open_world_tool() -> None:
    mcp = _MockMCP()
    register_tool(mcp)

    metadata = mcp.captured_tools["web_search"]
    assert metadata["title"] == "Web Search"
    assert metadata["structured_output"] is False
    annotations = metadata["annotations"]
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.open_world_hint is True


def test_tool_description_defines_use_and_non_use_boundaries() -> None:
    mcp = _MockMCP()
    register_tool(mcp)
    description = str(mcp.captured_tools["web_search"]["description"])

    assert "3-8 important words" in description
    assert "time_range" in description
    assert "Do not use it" in description
    assert "retry once" in description


def test_web_search_calls_duckduckgo_provider() -> None:
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=_StubProvider()) as mock_get:
        data = json.loads(func("hello"))

    mock_get.assert_called_once_with("duckduckgo")
    assert data["query"] == "hello"
    assert data["result_count"] == 1


def test_web_search_normalizes_query_and_forwards_limit() -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("  MCP   server\nrelease ", max_results=3)

    mock_search.assert_called_once_with("MCP server release", max_results=3)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (100, 20)],
)
def test_web_search_clamps_direct_python_result_limit(requested: int, expected: int) -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("query", max_results=requested)

    mock_search.assert_called_once_with("query", max_results=expected)


def test_web_search_passes_time_range_only_when_requested() -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("MCP release", time_range="week")

    mock_search.assert_called_once_with("MCP release", max_results=8, time_range="week")


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_web_search_rejects_empty_query(query: str) -> None:
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider") as mock_get:
        data = json.loads(func(query))

    mock_get.assert_not_called()
    assert data["error_code"] == "invalid_query"
    assert data["results"] == []
    assert data["result_count"] == 0


def test_web_search_rejects_overlong_query() -> None:
    data = json.loads(_get_web_search_func()("x" * 501))
    assert data["error_code"] == "invalid_query"
    assert "500" in data["error"]


def test_web_search_rejects_non_integer_limit() -> None:
    data = json.loads(_get_web_search_func()("query", max_results="many"))
    assert data["error_code"] == "invalid_max_results"


def test_web_search_returns_consistent_provider_unavailable_error() -> None:
    func = _get_web_search_func()
    with patch("server.tools.web_search.get_provider", side_effect=ValueError("Unknown provider")):
        data = json.loads(func("query"))

    assert data["query"] == "query"
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["error_code"] == "provider_unavailable"


def test_web_search_handles_invalid_provider_json() -> None:
    provider = MagicMock()
    provider.search.return_value = "not-json"
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=provider):
        data = json.loads(func("query"))

    assert data["error_code"] == "invalid_provider_response"


def test_real_duckduckgo_integration_via_tool() -> None:
    register("duckduckgo", DuckDuckGoProvider())

    with patch("server.providers.duckduckgo.DDGS") as mock_ddgs:
        instance = mock_ddgs.return_value.__enter__.return_value
        instance.text.return_value = [
            {"title": "D1", "href": "https://d1.example", "body": "body1"},
        ]
        with patch("server.providers.duckduckgo._rate_limit"):
            data = json.loads(_get_web_search_func()("ddg test"))

    assert data["query"] == "ddg test"
    assert data["result_count"] == 1
