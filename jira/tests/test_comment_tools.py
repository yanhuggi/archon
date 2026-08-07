"""Tests for the Jira comment mutation MCP tools."""

import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.add_comment import register as register_add
from server.tools.delete_comment import register as register_delete
from server.tools.update_comment import register as register_update


class StubProvider:
    def add_comment(self, issue_key: str, body: str, **kwargs) -> str:
        return json.dumps({"issue_key": issue_key, "added": True, "comment": {"id": "10001", "body": body}})

    def update_comment(self, issue_key: str, comment_id: str, body: str, **kwargs) -> str:
        return json.dumps({"issue_key": issue_key, "comment_id": comment_id, "updated": True})

    def delete_comment(self, issue_key: str, comment_id: str, **kwargs) -> str:
        return json.dumps({"issue_key": issue_key, "comment_id": comment_id, "deleted": True})


def get_func(register_tool, provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_add_comment_normalizes_key_and_preserves_body() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func(register_add)("test-1", "A note"))
    assert data["issue_key"] == "TEST-1"
    assert data["comment"]["body"] == "A note"


def test_update_comment_forwards_comment_id() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func(register_update)("test-1", "10001", "Edited note"))
    assert data["issue_key"] == "TEST-1"
    assert data["comment_id"] == "10001"


def test_delete_comment_forwards_comment_id() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func(register_delete)("test-1", "10001"))
    assert data == {"issue_key": "TEST-1", "comment_id": "10001", "deleted": True}


def test_comment_tools_reject_invalid_values() -> None:
    add_data = json.loads(get_func(register_add)("invalid", "note"))
    update_data = json.loads(get_func(register_update)("TEST-1", "abc", "note"))
    delete_data = json.loads(get_func(register_delete)("TEST-1", "abc"))
    assert add_data["error_code"] == "invalid_issue_key"
    assert update_data["error_code"] == "invalid_comment_id"
    assert delete_data["error_code"] == "invalid_comment_id"


def test_comment_tools_report_missing_provider() -> None:
    assert json.loads(get_func(register_add, "missing")("TEST-1", "note"))["error_code"] == "provider_unavailable"
    assert json.loads(get_func(register_update, "missing")("TEST-1", "10001", "note"))["error_code"] == "provider_unavailable"
    assert json.loads(get_func(register_delete, "missing")("TEST-1", "10001"))["error_code"] == "provider_unavailable"
