"""Tests for Jira workflow transition MCP tools."""

import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.get_transitions import register as register_get_transitions
from server.tools.transition_issue import register as register_transition_issue


class StubProvider:
    def get_transitions(self, issue_key: str, **kwargs) -> str:
        return json.dumps({
            "issue_key": issue_key,
            "transitions": [{"id": "31", "name": "Resolve Issue"}],
            "transition_count": 1,
        })

    def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        fields: dict[str, object] | None = None,
        **kwargs,
    ) -> str:
        return json.dumps({
            "issue_key": issue_key,
            "transition_id": transition_id,
            "transitioned": True,
            "fields": fields,
        })


def get_func(register_tool, provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_get_transitions_normalizes_issue_key() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func(register_get_transitions)("test-1"))
    assert data["issue_key"] == "TEST-1"
    assert data["transitions"][0]["id"] == "31"


def test_transition_issue_forwards_exact_id_and_fields() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func(register_transition_issue)(
        "test-1",
        "31",
        {"resolution": {"id": "1"}},
    ))
    assert data["issue_key"] == "TEST-1"
    assert data["transition_id"] == "31"
    assert data["fields"] == {"resolution": {"id": "1"}}


def test_transition_tools_reject_invalid_inputs() -> None:
    assert json.loads(get_func(register_get_transitions)("invalid"))["error_code"] == "invalid_issue_key"
    assert json.loads(get_func(register_transition_issue)("TEST-1", "done"))["error_code"] == "invalid_transition_id"


def test_transition_tools_report_missing_provider() -> None:
    assert json.loads(get_func(register_get_transitions, "missing")("TEST-1"))["error_code"] == "provider_unavailable"
    assert json.loads(get_func(register_transition_issue, "missing")("TEST-1", "31"))["error_code"] == "provider_unavailable"
