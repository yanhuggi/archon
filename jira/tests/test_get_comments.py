"""Tests for the get_comments MCP tool."""

import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.get_comments import register as register_tool


class StubProvider:
    def get_comments(self, issue_key: str, max_results: int, start_at: int, **kwargs) -> str:
        return json.dumps({
            "issue_key": issue_key,
            "total": 1,
            "start_at": start_at,
            "max_results": max_results,
            "comments": [{"author": "User", "body": "Decision"}],
            "comment_count": 1,
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_get_comments_returns_paginated_result() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()("test-1", max_results=10, start_at=5))
    assert data["issue_key"] == "TEST-1"
    assert data["max_results"] == 10
    assert data["start_at"] == 5
    assert data["comment_count"] == 1


def test_get_comments_rejects_invalid_key() -> None:
    data = json.loads(get_func()("invalid"))
    assert data["error_code"] == "invalid_issue_key"


def test_get_comments_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("TEST-1"))
    assert data["error_code"] == "provider_unavailable"
