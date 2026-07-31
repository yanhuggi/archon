"""Tests for the get_jql_value_suggestions MCP tool."""

import inspect
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.get_jql_value_suggestions import register as register_tool


class StubProvider:
    def get_jql_value_suggestions(self, field, query, max_results, refresh, **kwargs):
        return json.dumps(
            {
                "field": field,
                "query": query,
                "suggestions": [{"value": "Open"}],
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


def test_get_jql_value_suggestions_calls_provider() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()(" status ", query="op", refresh=True))
    assert data["field"] == "status"
    assert data["query"] == "op"
    assert data["refresh"] is True


def test_get_jql_value_suggestions_rejects_empty_field() -> None:
    data = json.loads(get_func()("  "))
    assert data["error_code"] == "invalid_jql_field"


def test_get_jql_value_suggestions_does_not_expose_provider() -> None:
    assert "provider" not in inspect.signature(get_func()).parameters
