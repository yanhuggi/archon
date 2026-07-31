"""Tests for the get_issue MCP tool."""

import inspect
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.get_issue import register as register_tool


class StubProvider:
    def get_issue(self, issue_key: str, **kwargs) -> str:
        return f"# {issue_key} - Example\n\n**关联任务：**\n- Blocks TEST-2"


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_get_issue_normalizes_key_and_returns_markdown() -> None:
    register("stub", StubProvider())
    result = get_func()("test-1")
    assert "# TEST-1" in result
    assert "Blocks TEST-2" in result


def test_get_issue_rejects_invalid_key() -> None:
    data = json.loads(get_func()("../admin"))
    assert data["error_code"] == "invalid_issue_key"


def test_get_issue_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("TEST-1"))
    assert data["error_code"] == "provider_unavailable"


def test_get_issue_does_not_expose_provider_argument() -> None:
    assert "provider" not in inspect.signature(get_func()).parameters


def test_get_issue_normalizes_provider_error() -> None:
    provider = StubProvider()
    provider.get_issue = MagicMock(return_value='{"error":"not found","error_code":"not_found"}')
    register("error", provider)
    data = json.loads(get_func("error")("TEST-1"))
    assert data["error_code"] == "not_found"
