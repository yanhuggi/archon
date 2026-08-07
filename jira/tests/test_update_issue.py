"""Tests for the update_issue MCP tool."""

import asyncio
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.update_issue import register as register_tool


class StubProvider:
    def update_issue(self, issue_key: str, fields: dict[str, object], **kwargs) -> str:
        return json.dumps({
            "issue_key": issue_key,
            "updated": True,
            "updated_fields": list(fields),
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_update_issue_normalizes_key_and_forwards_fields() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()("test-1", {"summary": "Updated", "assignee": None}))
    assert data == {
        "issue_key": "TEST-1",
        "updated": True,
        "updated_fields": ["summary", "assignee"],
    }


def test_update_issue_rejects_invalid_inputs() -> None:
    assert json.loads(get_func()("invalid", {"summary": "Updated"}))["error_code"] == "invalid_issue_key"
    assert json.loads(get_func()("TEST-1", {}))["error_code"] == "invalid_fields"


def test_update_issue_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("TEST-1", {"summary": "Updated"}))
    assert data["error_code"] == "provider_unavailable"


def test_update_issue_schema_marks_remote_write() -> None:
    from mcp.server import MCPServer

    server = MCPServer("test")
    register_tool(server, provider=StubProvider())
    tool = asyncio.run(server.list_tools())[0]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert tool.input_schema["required"] == ["issue_key", "fields"]
    assert "provider" not in tool.input_schema["properties"]
