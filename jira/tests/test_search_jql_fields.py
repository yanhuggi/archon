"""Tests for the search_jql_fields MCP tool."""

import inspect
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.search_jql_fields import register as register_tool


class StubProvider:
    def search_jql_fields(self, query, max_results, start_at, refresh, **kwargs):
        return json.dumps(
            {
                "query": query,
                "fields": [{"id": "status", "name": "Status"}],
                "result_count": 1,
                "refresh": refresh,
            }
        )


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_search_jql_fields_calls_provider() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()(" status ", refresh=True))
    assert data["query"] == "status"
    assert data["refresh"] is True


def test_search_jql_fields_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")())
    assert data["error_code"] == "provider_unavailable"


def test_search_jql_fields_does_not_expose_provider() -> None:
    assert "provider" not in inspect.signature(get_func()).parameters
