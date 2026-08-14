"""Tests for identity, permission, changelog, and issue-creation MCP tools."""

import asyncio
import json
from unittest.mock import MagicMock

from mcp.server import MCPServer

from server.providers import register
from server.tools.create_issue import register as register_create_issue
from server.tools.get_current_user import register as register_get_current_user
from server.tools.get_issue_changelog import register as register_get_issue_changelog
from server.tools.get_my_permissions import register as register_get_my_permissions


class StubProvider:
    def get_current_user(self, **kwargs) -> str:
        return json.dumps({"authenticated": True, "user": {"name": "user"}})

    def get_my_permissions(
        self,
        issue_key: str | None = None,
        project_key: str | None = None,
        permissions: list[str] | None = None,
        **kwargs,
    ) -> str:
        return json.dumps({
            "context": {"issue_key": issue_key, "project_key": project_key},
            "requested": permissions,
            "permissions": [],
            "permission_count": 0,
        })

    def get_issue_changelog(
        self,
        issue_key: str,
        max_results: int = 50,
        start_at: int = 0,
        **kwargs,
    ) -> str:
        return json.dumps({
            "issue_key": issue_key,
            "start_at": start_at,
            "max_results": max_results,
            "histories": [],
            "history_count": 0,
        })

    def create_issue(
        self,
        project_key: str,
        issue_type: str,
        fields: dict[str, object],
        idempotency_key: str | None = None,
        **kwargs,
    ) -> str:
        return json.dumps({
            "project_key": project_key,
            "issue_type": issue_type,
            "issue_key": f"{project_key}-1",
            "created": True,
            "created_fields": list(fields),
            "idempotency_key": idempotency_key,
        })


def get_func(register_tool, provider: str = "new-tools"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_read_tools_normalize_context_and_pagination() -> None:
    register("new-tools", StubProvider())

    user = json.loads(get_func(register_get_current_user)())
    permissions = json.loads(
        get_func(register_get_my_permissions)("saas-2762", None, [" DELETE_ISSUE "])
    )
    changelog = json.loads(get_func(register_get_issue_changelog)("saas-2762", 10, 5))

    assert user["authenticated"] is True
    assert permissions["context"]["issue_key"] == "SAAS-2762"
    assert permissions["requested"] == ["DELETE_ISSUE"]
    assert changelog["issue_key"] == "SAAS-2762"
    assert changelog["start_at"] == 5
    assert changelog["max_results"] == 10


def test_permission_tool_rejects_ambiguous_or_invalid_context() -> None:
    func = get_func(register_get_my_permissions)
    both = json.loads(func("SAAS-1", "SAAS"))
    invalid = json.loads(func(None, "not valid"))

    assert both["error_code"] == "invalid_permission_context"
    assert invalid["error_code"] == "invalid_project_key"


def test_create_issue_normalizes_project_and_preserves_fields() -> None:
    register("new-tools", StubProvider())
    data = json.loads(
        get_func(register_create_issue)(
            "saas",
            "编码任务",
            {"summary": "New issue", "description": "Details"},
            "submission-123",
        )
    )

    assert data["project_key"] == "SAAS"
    assert data["issue_key"] == "SAAS-1"
    assert data["created_fields"] == ["summary", "description"]
    assert data["idempotency_key"] == "submission-123"


def test_create_issue_rejects_invalid_inputs() -> None:
    func = get_func(register_create_issue)
    assert json.loads(func("not valid", "Task", {"summary": "x"}))["error_code"] == "invalid_project_key"
    assert json.loads(func("SAAS", "", {"summary": "x"}))["error_code"] == "invalid_issue_type"
    assert json.loads(func("SAAS", "Task", {}))["error_code"] == "invalid_fields"
    assert json.loads(func("SAAS", "Task", {"summary": "x"}, "bad\nkey"))[
        "error_code"
    ] == "invalid_idempotency_key"


def test_new_tool_schemas_disclose_read_and_write_boundaries() -> None:
    server = MCPServer("test")
    provider = StubProvider()
    register_get_current_user(server, provider=provider)
    register_get_my_permissions(server, provider=provider)
    register_get_issue_changelog(server, provider=provider)
    register_create_issue(server, provider=provider)

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert tools["get_current_user"].input_schema["properties"] == {}
    assert tools["get_my_permissions"].annotations.read_only_hint is True
    assert tools["get_issue_changelog"].annotations.read_only_hint is True
    assert tools["create_issue"].annotations.read_only_hint is False
    assert tools["create_issue"].annotations.destructive_hint is False
    assert tools["create_issue"].annotations.idempotent_hint is False
    assert tools["create_issue"].input_schema["required"] == [
        "project_key",
        "issue_type",
        "fields",
    ]
    assert tools["create_issue"].input_schema["properties"]["idempotency_key"]["anyOf"][0][
        "maxLength"
    ] == 200
