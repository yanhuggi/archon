"""Tests for the Blocks issue-link removal MCP tool."""

import asyncio
import json
from unittest.mock import MagicMock

from mcp.server import MCPServer

from server.providers import register
from server.tools.remove_issue_blocks import register as register_tool


class StubProvider:
    def remove_issue_blocks(self, source_issue_key: str, target_issue_key: str, **kwargs) -> str:
        return json.dumps({
            "source_issue_key": source_issue_key,
            "target_issue_key": target_issue_key,
            "link_type": "Blocks",
            "link_id": "10001",
            "removed": True,
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_remove_issue_blocks_normalizes_keys_and_preserves_direction() -> None:
    register("stub", StubProvider())

    data = json.loads(get_func()("ylzhxt-19183", "ylzhxt-16820"))

    assert data == {
        "source_issue_key": "YLZHXT-19183",
        "target_issue_key": "YLZHXT-16820",
        "link_type": "Blocks",
        "link_id": "10001",
        "removed": True,
    }


def test_remove_issue_blocks_rejects_invalid_or_identical_keys() -> None:
    assert json.loads(get_func()("not an issue", "TEST-2"))["error_code"] == "invalid_issue_key"
    assert json.loads(get_func()("TEST-1", "test-1"))["error_code"] == "invalid_issue_link"


def test_remove_issue_blocks_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("TEST-1", "TEST-2"))
    assert data["error_code"] == "provider_unavailable"
    assert data["removed"] is False


def test_remove_issue_blocks_schema_marks_destructive_remote_write() -> None:
    server = MCPServer("test")
    register_tool(server, provider=StubProvider())

    tool = asyncio.run(server.list_tools())[0]

    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert tool.annotations.idempotent_hint is True
    assert tool.input_schema["required"] == ["source_issue_key", "target_issue_key"]
    assert "provider" not in tool.input_schema["properties"]
