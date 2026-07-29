"""Tests for server.tools.search_issues — search_issues MCP tool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.jira import JiraProvider
from server.tools.search_issues import register as register_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock httpx.Client with is_closed=False."""
    client = MagicMock()
    client.is_closed = False
    return client


class _StubProvider:
    """Minimal stub that satisfies the JiraProvider protocol."""

    def search_issues(self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return json.dumps({
            "jql": jql,
            "total": 1,
            "start_at": start_at,
            "max_results": max_results,
            "results": [{"key": "STUB-1", "summary": "stub task"}],
            "result_count": 1,
        })

    def get_issue(self, issue_key: str, **kwargs) -> str:
        return "{}"

    def get_comments(self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_attachment(self, attachment_id: str, save_to: str = "", **kwargs) -> str:
        return "{}"


@pytest.fixture(autouse=True)
def _register_stub() -> None:
    register("stub", _StubProvider())


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
    assert func.__name__ == "search_issues"


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


def test_tool_description_mentions_jql() -> None:
    mcp = _MockMCP()
    register_tool(mcp)
    desc = mcp.captured_tools["search_issues"]["description"].lower()
    assert "jql" in desc
    assert "search" in desc


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------


def _get_func(default_provider: str = "stub") -> callable:
    mcp = MagicMock(spec=["tool"])
    inner_decorator = MagicMock()
    mcp.tool = lambda **kw: inner_decorator
    register_tool(mcp, default_provider=default_provider)
    return inner_decorator.call_args[0][0]


def test_search_issues_calls_provider() -> None:
    func = _get_func()
    with patch("server.tools.search_issues.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProvider()
        result = func("project = PROJ")
        data = json.loads(result)

    mock_get.assert_called_once_with("stub")
    assert data["result_count"] == 1


def test_search_issues_clamps_max_results() -> None:
    func = _get_func()
    stub = _StubProvider()

    with patch("server.tools.search_issues.get_provider", return_value=stub):
        with patch.object(stub, "search_issues", wraps=stub.search_issues) as mock_search:
            func("jql", max_results=500)
    mock_search.assert_called_once_with("jql", max_results=200, start_at=0)

    with patch("server.tools.search_issues.get_provider", return_value=stub):
        with patch.object(stub, "search_issues", wraps=stub.search_issues) as mock_search:
            func("jql", max_results=0)
    mock_search.assert_called_once_with("jql", max_results=1, start_at=0)


def test_search_issues_unknown_provider() -> None:
    func = _get_func()
    result = func("jql", provider="nonexistent")
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
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "total": 1,
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Fix bug",
                        "status": {"name": "Open"},
                        "assignee": {"displayName": "John"},
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "High"},
                        "labels": [],
                        "created": "2025-01-15T10:30:00.000+0800",
                        "updated": "2025-01-20T14:00:00.000+0800",
                    },
                }
            ],
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()

    func = _get_func("jira")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = func("project = PROJ", provider="jira")
    data = json.loads(result)

    assert data["result_count"] == 1
    assert data["results"][0]["key"] == "PROJ-1"
