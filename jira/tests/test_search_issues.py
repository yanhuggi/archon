"""Tests for the search_issues MCP tool."""

import inspect
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.search_issues import register as register_tool


class StubProvider:
    def search_issues(self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return json.dumps({
            "jql": jql,
            "total": 1,
            "start_at": start_at,
            "max_results": max_results,
            "results": [{"key": "TEST-1", "summary": "Example"}],
            "result_count": 1,
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_search_calls_provider_and_preserves_jql_content() -> None:
    provider = StubProvider()
    register("stub", provider)
    result = json.loads(get_func()('  summary ~ "foo   bar"  ', max_results=5))
    assert result["jql"] == 'summary ~ "foo   bar"'
    assert result["result_count"] == 1


def test_search_rejects_empty_jql() -> None:
    data = json.loads(get_func()("   "))
    assert data["error_code"] == "invalid_jql"


def test_search_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("project = TEST"))
    assert data["error_code"] == "provider_unavailable"


def test_search_does_not_expose_provider_argument() -> None:
    assert "provider" not in inspect.signature(get_func()).parameters


def test_search_handles_invalid_provider_json() -> None:
    provider = StubProvider()
    provider.search_issues = MagicMock(return_value="invalid")
    register("bad", provider)
    data = json.loads(get_func("bad")("project = TEST"))
    assert data["error_code"] == "invalid_provider_response"
