"""Tests for server.tools.get_issue — get_issue MCP tool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.jira import JiraProvider
from server.tools.get_issue import register as register_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock httpx.Client with is_closed=False."""
    client = MagicMock()
    client.is_closed = False
    return client


class _StubProvider:
    def search_issues(self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_issue(self, issue_key: str, **kwargs) -> str:
        return (
            f"# {issue_key} - stub issue\n\n"
            "| 字段 | 值 |\n|---|---|\n| 状态 | Open |\n"
        )

    def get_comments(self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_attachment(self, attachment_id: str, save_to: str = "", **kwargs) -> str:
        return "{}"


class _StubProviderWithLinks:
    def search_issues(self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_issue(self, issue_key: str, **kwargs) -> str:
        return (
            f"# {issue_key} - issue with links\n\n"
            "| 字段 | 值 |\n|---|---|\n| 状态 | Open |\n\n"
            "**关联任务：**\n\n"
            "- Blocks PROJ-10 Deploy (Open)\n"
            "- is blocked by PROJ-5 Migration (Done)\n\n"
            "**子任务：**\n\n"
            "- PROJ-2 Sub (Done)\n\n"
            "**附件：**\n\n"
            "- pic.png (0.0 MB) by  - \n"
        )

    def get_comments(self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_attachment(self, attachment_id: str, save_to: str = "", **kwargs) -> str:
        return "{}"


@pytest.fixture(autouse=True)
def _register_stubs() -> None:
    register("stub", _StubProvider())
    register("stub_links", _StubProviderWithLinks())


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_register_creates_tool() -> None:
    mcp = MagicMock()
    register_tool(mcp, default_provider="stub")
    mcp.tool.assert_called_once()
    inner_decorator = mcp.tool.return_value
    inner_decorator.assert_called_once()
    func = inner_decorator.call_args[0][0]
    assert func.__name__ == "get_issue"


class _MockMCP:
    def __init__(self) -> None:
        self.captured_tools: dict[str, dict] = {}

    def tool(self, **kwargs: object) -> callable:
        tool_meta = kwargs

        def decorator(func: callable) -> callable:
            self.captured_tools[func.__name__] = {
                "function": func,
                "description": tool_meta.get("description", ""),
            }
            return func

        return decorator


def test_tool_description_mentions_links() -> None:
    mcp = _MockMCP()
    register_tool(mcp)
    desc = mcp.captured_tools["get_issue"]["description"].lower()
    assert "link" in desc
    assert "sub-task" in desc or "subtask" in desc


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------


def _get_func(default_provider: str = "stub") -> callable:
    mcp = MagicMock(spec=["tool"])
    inner_decorator = MagicMock()
    mcp.tool = lambda **kw: inner_decorator
    register_tool(mcp, default_provider=default_provider)
    return inner_decorator.call_args[0][0]


def test_get_issue_calls_provider() -> None:
    func = _get_func()
    with patch("server.tools.get_issue.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProvider()
        result = func("PROJ-1")

    mock_get.assert_called_once_with("stub")
    assert "# PROJ-1" in result
    assert "stub issue" in result


def test_get_issue_with_links_and_subtasks() -> None:
    func = _get_func("stub_links")
    with patch("server.tools.get_issue.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProviderWithLinks()
        result = func("PROJ-1")

    assert "Blocks PROJ-10" in result
    assert "is blocked by PROJ-5" in result
    assert "PROJ-2" in result
    assert "pic.png" in result


def test_get_issue_unknown_provider() -> None:
    func = _get_func()
    result = func("PROJ-1", provider="nonexistent")
    assert "Unknown provider" in result


# ---------------------------------------------------------------------------
# Integration with real provider
# ---------------------------------------------------------------------------


def test_real_provider_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")
    register("jira", JiraProvider())

    mock_client = _make_mock_client()
    # First call: get issue
    issue_resp = MagicMock(
        status_code=200,
        json=lambda: {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test issue",
                "description": "Desc",
                "status": {"name": "Open"},
                "assignee": {"displayName": "John"},
                "reporter": {"displayName": "Jane"},
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "labels": ["urgent"],
                "created": "2025-01-15T10:30:00.000+0800",
                "updated": "2025-01-20T14:00:00.000+0800",
                "parent": None,
                "subtasks": [],
                "issuelinks": [],
                "attachment": [],
            },
        },
    )
    issue_resp.raise_for_status = MagicMock()
    # Second call: field map
    field_resp = MagicMock(status_code=200)
    field_resp.json.return_value = [
        {"id": "customfield_10204", "name": "开发人员", "custom": True},
    ]
    field_resp.raise_for_status = MagicMock()
    mock_client.get.side_effect = [field_resp, issue_resp]

    func = _get_func("jira")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = func("PROJ-1", provider="jira")

    assert "# PROJ-1" in result
    assert "John" in result
