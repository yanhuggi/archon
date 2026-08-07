"""Tests for server.providers.jira — JiraProvider implementation."""

import base64
import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from server.config import JiraConfig
from server.providers.jira import (
    _DEFAULT_MAX_ATTACHMENT_SIZE,
    _MAX_FIELD_LENGTH,
    _MAX_INLINE_VALUES,
    _MAX_ISSUE_CHARS,
    _MAX_LIST_ITEMS,
    _MAX_OTHER_FIELD_LENGTH,
    _MAX_OTHER_FIELD_ROWS,
    _MAX_SEARCH_RESULTS,
    JiraProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock httpx.Client with is_closed=False."""
    client = MagicMock()
    client.is_closed = False
    return client


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_provider_is_protocol_compliant() -> None:
    """JiraProvider satisfies the runtime-checkable protocol."""
    provider = JiraProvider()
    assert isinstance(provider, JiraProvider)
    provider.close()


# ---------------------------------------------------------------------------
# search_issues
# ---------------------------------------------------------------------------


def test_search_issues_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_issues returns formatted results from Jira API."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
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
                        "labels": ["urgent"],
                        "created": "2025-01-15T10:30:00.000+0800",
                        "updated": "2025-01-20T14:00:00.000+0800",
                    },
                }
            ],
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    provider._client = mock_client

    result = json.loads(provider.search_issues("project = PROJ"))
    assert result["total"] == 1
    assert result["results"][0]["key"] == "PROJ-1"
    assert result["results"][0]["assignee"] == "John"
    provider.close()


def test_search_issues_unassigned(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_issues handles unassigned issues."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "total": 1,
            "issues": [
                {
                    "key": "PROJ-2",
                    "fields": {
                        "summary": "Unassigned task",
                        "status": {"name": "Open"},
                        "assignee": None,
                        "issuetype": {"name": "Task"},
                        "priority": {"name": "Low"},
                        "labels": [],
                        "created": "2025-01-15T10:30:00.000+0800",
                        "updated": "2025-01-20T14:00:00.000+0800",
                    },
                }
            ],
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    provider._client = mock_client

    result = json.loads(provider.search_issues("project = PROJ"))
    assert result["results"][0]["assignee"] == "Unassigned"
    provider.close()


def test_search_issues_clamps_max_results() -> None:
    """search_issues clamps max_results to 1-200."""
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"total": 0, "issues": []},
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    provider._client = mock_client

    # Test upper clamp
    provider.search_issues("jql", max_results=500)
    call_args = mock_client.get.call_args
    assert call_args[1]["params"]["maxResults"] == _MAX_SEARCH_RESULTS

    # Test lower clamp
    provider.search_issues("jql", max_results=0)
    call_args = mock_client.get.call_args
    assert call_args[1]["params"]["maxResults"] == 1

    provider.close()


def test_search_issues_rejects_malformed_issue_list() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    response = MagicMock(status_code=200)
    response.json.return_value = {"total": 1, "issues": {"key": "PROJ-1"}}
    response.raise_for_status = MagicMock()
    mock_client.get.return_value = response
    provider._client = mock_client

    data = json.loads(provider.search_issues("project = PROJ"))
    assert data["error_code"] == "invalid_provider_response"
    provider.close()


# ---------------------------------------------------------------------------
# get_issue
# ---------------------------------------------------------------------------


def test_get_issue_with_links_and_subtasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_issue normalizes issue links and extracts subtasks."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test issue",
                "description": "Description text",
                "status": {"name": "Open"},
                "assignee": {"displayName": "John"},
                "reporter": {"displayName": "Jane"},
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "labels": ["test"],
                "created": "2025-01-15T10:30:00.000+0800",
                "updated": "2025-01-20T14:00:00.000+0800",
                "parent": {"key": "PROJ-100", "fields": {"summary": "Epic"}},
                "subtasks": [
                    {"key": "PROJ-2", "fields": {"summary": "Sub", "status": {"name": "Done"}}}
                ],
                "issuelinks": [
                    {
                        "type": {"name": "Blocks", "outward": "Blocks", "inward": "is blocked by"},
                        "outwardIssue": {"key": "PROJ-10", "fields": {"summary": "Deploy", "status": {"name": "Open"}}},
                    },
                    {
                        "type": {"name": "Blocks", "outward": "Blocks", "inward": "is blocked by"},
                        "inwardIssue": {"key": "PROJ-5", "fields": {"summary": "Migration", "status": {"name": "Done"}}},
                    },
                ],
                "attachment": [
                    {"id": "10001", "filename": "pic.png", "size": 1024, "mimeType": "image/png", "author": {"displayName": "John"}, "created": "2025-01-16T12:00:00.000+0800"},
                ],
            },
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    # Mock field map endpoint
    field_resp = MagicMock()
    field_resp.status_code = 200
    field_resp.json.return_value = []
    field_resp.raise_for_status = MagicMock()
    mock_client.get.side_effect = [field_resp, mock_client.get.return_value]
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")
    assert "# PROJ-1" in result
    assert "Blocks PROJ-10" in result
    assert "is blocked by PROJ-5" in result
    assert "PROJ-2" in result
    assert "PROJ-100" in result
    assert "pic.png" in result
    provider.close()


def test_get_issue_description_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_issue truncates long descriptions."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    long_desc = "x" * 3000
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "key": "PROJ-1",
            "fields": {
                "summary": "Test",
                "description": long_desc,
                "status": {"name": "Open"},
                "assignee": None,
                "reporter": None,
                "issuetype": {"name": "Task"},
                "priority": None,
                "labels": [],
                "created": "",
                "updated": "",
                "parent": None,
                "subtasks": [],
                "issuelinks": [],
                "attachment": [],
            },
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    field_resp = MagicMock()
    field_resp.status_code = 200
    field_resp.json.return_value = []
    field_resp.raise_for_status = MagicMock()
    mock_client.get.side_effect = [field_resp, mock_client.get.return_value]
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")
    assert "..." in result
    assert result.count("x") == _MAX_FIELD_LENGTH
    provider.close()


def test_get_issue_custom_field_truncation() -> None:
    """get_issue bounds custom fields before returning model context."""
    provider = JiraProvider()
    mock_client = _make_mock_client()
    field_resp = MagicMock(status_code=200)
    field_resp.json.return_value = [
        {"id": "customfield_99999", "name": "Long field", "custom": True}
    ]
    field_resp.raise_for_status = MagicMock()
    issue_resp = MagicMock(status_code=200)
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "description": None,
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "customfield_99999": "x" * (_MAX_FIELD_LENGTH + 100),
        },
    }
    issue_resp.raise_for_status = MagicMock()
    mock_client.get.side_effect = [field_resp, issue_resp]
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")

    assert "Long field" in result
    # Table rows carry a tighter bound than free-form sections.
    assert result.count("x") == _MAX_OTHER_FIELD_LENGTH
    assert "..." in result
    provider.close()


def test_get_issue_json_skips_the_unused_custom_field_lookup() -> None:
    """The export projection renders no custom fields, so it must not fetch them."""
    provider = JiraProvider(
        JiraConfig(url="https://jira.example.com", username="user", password="pass")
    )
    mock_client = _make_mock_client()
    issue_resp = MagicMock(status_code=200)
    issue_resp.raise_for_status = MagicMock()
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {"summary": "Test", "status": {"name": "Open"}},
    }
    mock_client.get.return_value = issue_resp
    provider._client = mock_client

    result = provider.get_issue_json("PROJ-1")

    assert result["key"] == "PROJ-1"
    assert [call.args[0] for call in mock_client.get.call_args_list] == ["rest/api/2/issue/PROJ-1"]
    assert "customfield" not in mock_client.get.call_args.kwargs["params"]["fields"]
    provider.close()


def test_get_issue_bounds_a_wide_custom_field_instance() -> None:
    """get_issue caps the auxiliary table so one issue cannot flood context."""
    provider = JiraProvider()
    field_map = {f"customfield_{20000 + index}": f"Field {index}" for index in range(300)}
    provider._field_map = dict(field_map)
    provider._field_map_fetched_at = float("inf")
    mock_client = _make_mock_client()
    issue_resp = MagicMock(status_code=200)
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            **{field_id: "x" * 3000 for field_id in field_map},
        },
    }
    issue_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = issue_resp
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")

    assert result.count("\n| Field ") == _MAX_OTHER_FIELD_ROWS
    assert f"（另有 {300 - _MAX_OTHER_FIELD_ROWS} 个自定义字段未显示）" in result
    assert len(result) < 60_000
    provider.close()


def test_get_issue_bounds_every_unbounded_section() -> None:
    """Lists, multi-value fields and the summary all obey a budget, not just the table."""
    provider = JiraProvider()
    provider._field_map = {}
    provider._field_map_fetched_at = float("inf")
    count = 400
    mock_client = _make_mock_client()
    issue_resp = MagicMock(status_code=200)
    issue_resp.raise_for_status = MagicMock()
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {
            "summary": "S" * 5000,
            "status": {"name": "Open"},
            "issuetype": {"name": "Bug"},
            "labels": [f"label-{index}" * 20 for index in range(count)],
            "components": [{"name": f"comp{index}"} for index in range(count)],
            "versions": [{"name": f"v{index}"} for index in range(count)],
            "subtasks": [
                {"key": f"SUB-{index}", "fields": {"summary": "X" * 200}} for index in range(count)
            ],
            "issuelinks": [
                {
                    "type": {"outward": "blocks"},
                    "outwardIssue": {"key": f"L-{index}", "fields": {"summary": "Y" * 200}},
                }
                for index in range(count)
            ],
            "attachment": [
                {"id": str(index), "filename": "f" * 200, "size": 1} for index in range(count)
            ],
        },
    }
    mock_client.get.return_value = issue_resp

    provider._client = mock_client
    result = provider.get_issue("PROJ-1")

    assert len(result) <= _MAX_ISSUE_CHARS
    assert result.count("\n- SUB-") == _MAX_LIST_ITEMS
    assert result.count("\n- blocks L-") == _MAX_LIST_ITEMS
    assert f"（另有 {count - _MAX_LIST_ITEMS} 个子任务未显示）" in result
    assert f"（另有 {count - _MAX_LIST_ITEMS} 条关联未显示）" in result
    assert f"(+{count - _MAX_INLINE_VALUES})" in result  # capped multi-value fields
    assert result.count("S") < 5000  # summary truncated in the heading
    provider.close()


def test_get_issue_escapes_table_breaking_field_values() -> None:
    """Untrusted Jira values cannot forge extra Markdown table cells or rows."""
    provider = JiraProvider()
    provider._field_map = {}
    provider._field_map_fetched_at = float("inf")
    mock_client = _make_mock_client()
    issue_resp = MagicMock(status_code=200)
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "Open | Forged | Column"},
            "issuetype": {"name": "Task"},
            "labels": ["a|b"],
            "created": "2025-01-15\n| 更新时间 | forged |",
        },
    }
    issue_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = issue_resp
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")

    assert "| 状态 | Open \\| Forged \\| Column |" in result
    assert "| 标签 | a\\|b |" in result
    assert "| 更新时间 | forged |" not in result
    provider.close()


def test_custom_field_map_is_cached_per_provider() -> None:
    provider = JiraProvider(JiraConfig(jql_field_refresh_interval=60))
    mock_client = _make_mock_client()
    field_resp = MagicMock(status_code=200)
    field_resp.json.return_value = [
        {"id": "customfield_1", "name": "Team", "custom": True},
        {"id": "summary", "name": "Summary", "custom": False},
    ]
    field_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = field_resp
    provider._client = mock_client

    assert provider._get_field_map() == {"customfield_1": "Team"}
    assert provider._get_field_map() == {"customfield_1": "Team"}
    mock_client.get.assert_called_once_with("rest/api/2/field")
    provider.close()


def test_get_issue_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_issue handles issues with missing optional fields gracefully."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "key": "PROJ-1",
            "fields": {
                "summary": "Minimal issue",
                "description": None,
                "status": {"name": "Open"},
                "assignee": None,
                "reporter": None,
                "issuetype": {"name": "Task"},
                "priority": None,
                "labels": [],
                "created": "",
                "updated": "",
            },
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    field_resp = MagicMock()
    field_resp.status_code = 200
    field_resp.json.return_value = []
    field_resp.raise_for_status = MagicMock()
    mock_client.get.side_effect = [field_resp, mock_client.get.return_value]
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")
    assert "# PROJ-1" in result
    assert "未分配" in result  # unassigned shows as 未分配
    assert "描述" not in result  # no description section when empty
    provider.close()


def test_get_issue_field_map_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_issue still returns standard fields when field map fetch fails."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    issue_resp = MagicMock(status_code=200)
    issue_resp.json.return_value = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test", "description": "Desc", "status": {"name": "Open"},
            "assignee": {"displayName": "John"}, "reporter": None,
            "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
            "labels": [], "created": "", "updated": "",
            "subtasks": [], "issuelinks": [], "attachment": [],
        },
    }
    issue_resp.raise_for_status = MagicMock()
    field_resp = MagicMock(status_code=500)
    field_resp.raise_for_status.side_effect = Exception("Server error")
    mock_client.get.side_effect = [field_resp, issue_resp]
    provider._client = mock_client

    result = provider.get_issue("PROJ-1")
    assert "# PROJ-1" in result
    assert "John" in result
    assert "其他信息" not in result  # no custom fields section when map fails
    provider.close()


# ---------------------------------------------------------------------------
# workflow transitions
# ---------------------------------------------------------------------------


def _transitions_response() -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "transitions": [
            {
                "id": "31",
                "name": "Resolve Issue",
                "to": {"id": "5", "name": "Resolved"},
                "fields": {
                    "resolution": {
                        "name": "Resolution",
                        "required": True,
                        "schema": {"type": "resolution"},
                        "operations": ["set"],
                        "allowedValues": [{"id": "1", "name": "Fixed"}],
                    }
                },
            }
        ]
    }
    response.raise_for_status = MagicMock()
    return response


def test_get_transitions_success() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = _transitions_response()
    provider._client = mock_client

    data = json.loads(provider.get_transitions("PROJ-1"))

    assert data["transition_count"] == 1
    assert data["transitions"][0]["id"] == "31"
    assert data["transitions"][0]["to"] == {"id": "5", "name": "Resolved"}
    assert data["transitions"][0]["fields"][0]["required"] is True
    assert data["transitions"][0]["fields"][0]["allowed_values"] == [
        {"id": "1", "name": "Fixed"}
    ]
    mock_client.get.assert_called_once_with(
        "rest/api/2/issue/PROJ-1/transitions",
        params={"expand": "transitions.fields"},
    )
    provider.close()


def test_transition_issue_success_with_fields() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = _transitions_response()
    post_response = MagicMock(status_code=204)
    post_response.raise_for_status = MagicMock()
    mock_client.post.return_value = post_response
    provider._client = mock_client

    data = json.loads(provider.transition_issue(
        "PROJ-1",
        "31",
        fields={"resolution": {"id": "1"}},
    ))

    assert data["transitioned"] is True
    assert data["transition"]["name"] == "Resolve Issue"
    mock_client.post.assert_called_once_with(
        "rest/api/2/issue/PROJ-1/transitions",
        json={
            "transition": {"id": "31"},
            "fields": {"resolution": {"id": "1"}},
        },
    )
    provider.close()


def test_transition_issue_rejects_unavailable_transition_before_post() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = _transitions_response()
    provider._client = mock_client

    data = json.loads(provider.transition_issue("PROJ-1", "99"))

    assert data["error_code"] == "transition_unavailable"
    assert data["available_transitions"][0]["id"] == "31"
    mock_client.post.assert_not_called()
    provider.close()


def test_transition_issue_rejects_unavailable_fields_before_post() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = _transitions_response()
    provider._client = mock_client

    data = json.loads(provider.transition_issue(
        "PROJ-1",
        "31",
        fields={"assignee": {"name": "john"}},
    ))

    assert data["error_code"] == "unavailable_transition_fields"
    assert data["unavailable_fields"] == ["assignee"]
    assert data["available_fields"] == ["resolution"]
    mock_client.post.assert_not_called()
    provider.close()


def test_transition_issue_rejects_invalid_id_without_request() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    provider._client = mock_client

    data = json.loads(provider.transition_issue("PROJ-1", "done"))

    assert data["error_code"] == "invalid_transition_id"
    mock_client.get.assert_not_called()
    mock_client.post.assert_not_called()
    provider.close()


# ---------------------------------------------------------------------------
# get_comments
# ---------------------------------------------------------------------------


def test_get_comments_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_comments returns formatted comments."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "total": 1,
            "comments": [
                {
                    "id": "10001",
                    "author": {"displayName": "John"},
                    "body": "Test comment",
                    "created": "2025-01-16T10:00:00.000+0800",
                    "updated": "2025-01-16T10:00:00.000+0800",
                }
            ],
        },
    )
    mock_client.get.return_value.raise_for_status = MagicMock()
    provider._client = mock_client

    result = json.loads(provider.get_comments("PROJ-1"))
    assert result["issue_key"] == "PROJ-1"
    assert result["total"] == 1
    assert result["comments"][0]["id"] == "10001"
    assert result["comments"][0]["author"] == "John"
    provider.close()


def test_get_comments_rejects_malformed_comments_list() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    response = MagicMock(status_code=200)
    response.json.return_value = {"total": 1, "comments": {"body": "bad"}}
    response.raise_for_status = MagicMock()
    mock_client.get.return_value = response
    provider._client = mock_client

    data = json.loads(provider.get_comments("PROJ-1"))
    assert data["error_code"] == "invalid_provider_response"
    provider.close()


# ---------------------------------------------------------------------------
# comment mutations
# ---------------------------------------------------------------------------


def _comment_response(comment_id: str = "10001", body: str = "Updated") -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "id": comment_id,
        "author": {"displayName": "John"},
        "body": body,
        "created": "2025-01-16T10:00:00.000+0800",
        "updated": "2025-01-16T11:00:00.000+0800",
    }
    response.raise_for_status = MagicMock()
    return response


def test_add_comment_success() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.post.return_value = _comment_response(body="New comment")
    provider._client = mock_client

    data = json.loads(provider.add_comment("PROJ-1", "New comment"))

    assert data["added"] is True
    assert data["comment"]["id"] == "10001"
    assert data["comment"]["body"] == "New comment"
    mock_client.post.assert_called_once_with(
        "rest/api/2/issue/PROJ-1/comment",
        json={"body": "New comment"},
    )
    provider.close()


def test_update_comment_success() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.put.return_value = _comment_response()
    provider._client = mock_client

    data = json.loads(provider.update_comment("PROJ-1", "10001", "Updated"))

    assert data["updated"] is True
    assert data["comment_id"] == "10001"
    mock_client.put.assert_called_once_with(
        "rest/api/2/issue/PROJ-1/comment/10001",
        json={"body": "Updated"},
    )
    provider.close()


def test_delete_comment_success() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    response = MagicMock(status_code=204)
    response.raise_for_status = MagicMock()
    mock_client.delete.return_value = response
    provider._client = mock_client

    data = json.loads(provider.delete_comment("PROJ-1", "10001"))

    assert data == {"issue_key": "PROJ-1", "comment_id": "10001", "deleted": True}
    mock_client.delete.assert_called_once_with("rest/api/2/issue/PROJ-1/comment/10001")
    provider.close()


def test_comment_mutations_reject_invalid_values_without_request() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    provider._client = mock_client

    assert json.loads(provider.add_comment("PROJ-1", "  "))["error_code"] == "invalid_comment_body"
    assert json.loads(provider.update_comment("PROJ-1", "abc", "body"))["error_code"] == "invalid_comment_id"
    assert json.loads(provider.delete_comment("PROJ-1", "abc"))["error_code"] == "invalid_comment_id"
    mock_client.post.assert_not_called()
    mock_client.put.assert_not_called()
    mock_client.delete.assert_not_called()
    provider.close()


# ---------------------------------------------------------------------------
# update_issue
# ---------------------------------------------------------------------------


def test_update_issue_success() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    editmeta_response = MagicMock(status_code=200)
    editmeta_response.json.return_value = {
        "fields": {
            "summary": {"name": "Summary", "operations": ["set"]},
            "customfield_11122": {"name": "Test notes", "operations": ["set"]},
        }
    }
    editmeta_response.raise_for_status = MagicMock()
    mock_client.get.return_value = editmeta_response
    response = MagicMock(status_code=204)
    response.raise_for_status = MagicMock()
    mock_client.put.return_value = response
    provider._client = mock_client

    data = json.loads(provider.update_issue(
        "PROJ-1",
        {"summary": "Updated summary", "customfield_11122": None},
    ))

    assert data == {
        "issue_key": "PROJ-1",
        "updated": True,
        "updated_fields": ["summary", "customfield_11122"],
    }
    mock_client.put.assert_called_once_with(
        "rest/api/2/issue/PROJ-1",
        json={"fields": {"summary": "Updated summary", "customfield_11122": None}},
    )
    provider.close()


def test_update_issue_rejects_invalid_fields_without_request() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    provider._client = mock_client

    assert json.loads(provider.update_issue("PROJ-1", {}))["error_code"] == "invalid_fields"
    assert json.loads(provider.update_issue("PROJ-1", {" ": "value"}))["error_code"] == "invalid_fields"
    assert mock_client.put.call_count == 0
    provider.close()


def test_update_issue_rejects_uneditable_fields_before_put() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    editmeta_response = MagicMock(status_code=200)
    editmeta_response.json.return_value = {
        "fields": {
            "summary": {"name": "Summary", "operations": ["set"]},
            "priority": {"name": "Priority", "operations": []},
        }
    }
    editmeta_response.raise_for_status = MagicMock()
    mock_client.get.return_value = editmeta_response
    provider._client = mock_client

    data = json.loads(provider.update_issue("PROJ-1", {"summary": "Updated", "priority": "High"}))

    assert data["error_code"] == "uneditable_fields"
    assert data["uneditable_fields"] == ["priority"]
    assert data["editable_fields"] == ["summary"]
    mock_client.put.assert_not_called()
    provider.close()


def test_update_issue_does_not_put_when_editmeta_fails() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    request = httpx.Request(
        "GET",
        "https://jira.example.com/rest/api/2/issue/PROJ-1/editmeta",
    )
    mock_client.get.return_value = httpx.Response(
        403,
        request=request,
        json={"errorMessages": ["Editing is forbidden"]},
    )
    provider._client = mock_client

    data = json.loads(provider.update_issue("PROJ-1", {"summary": "Updated"}))

    assert data["error_code"] == "authentication_error"
    assert "Editing is forbidden" in data["error"]
    mock_client.put.assert_not_called()
    provider.close()


def test_update_issue_returns_jira_validation_details() -> None:
    provider = JiraProvider()
    mock_client = _make_mock_client()
    editmeta_response = MagicMock(status_code=200)
    editmeta_response.json.return_value = {
        "fields": {"priority": {"name": "Priority", "operations": ["set"]}}
    }
    editmeta_response.raise_for_status = MagicMock()
    mock_client.get.return_value = editmeta_response
    request = httpx.Request("PUT", "https://jira.example.com/rest/api/2/issue/PROJ-1")
    response = httpx.Response(
        400,
        request=request,
        json={"errors": {"priority": "Priority is required"}},
    )
    mock_client.put.return_value = response
    provider._client = mock_client

    data = json.loads(provider.update_issue("PROJ-1", {"priority": None}))
    assert data["error_code"] == "upstream_error"
    assert "priority: Priority is required" in data["error"]
    provider.close()


# ---------------------------------------------------------------------------
# get_attachment
# ---------------------------------------------------------------------------


def test_download_attachment_saves_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """download_attachment downloads and saves file to disk."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()

    # metadata call
    meta_resp = MagicMock()
    meta_resp.status_code = 200
    meta_resp.json.return_value = {
        "id": "10001",
        "filename": "error.log",
        "size": 22,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/secure/attachment/10001/error.log",
    }
    meta_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = meta_resp

    # stream download
    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()
    stream_resp.__enter__ = MagicMock(return_value=stream_resp)
    stream_resp.__exit__ = MagicMock(return_value=False)
    stream_resp.iter_bytes.return_value = [b"Error: connection refused"]
    mock_client.stream.return_value = stream_resp

    save_path = str(tmp_path / "error.log")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = json.loads(provider.download_attachment("10001", save_to=save_path))
    assert result["filename"] == "error.log"
    assert result["saved_to"] == save_path
    assert os.path.exists(save_path)
    with open(save_path, "rb") as f:
        assert f.read() == b"Error: connection refused"
    provider.close()


def test_get_attachment_reads_content_without_writing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """get_attachment returns bounded content and does not need an output path."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    meta_resp = MagicMock(status_code=200)
    meta_resp.json.return_value = {
        "filename": "error.log",
        "size": 5,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/secure/attachment/10001/error.log",
    }
    meta_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = meta_resp
    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()
    stream_resp.__enter__ = MagicMock(return_value=stream_resp)
    stream_resp.__exit__ = MagicMock(return_value=False)
    stream_resp.iter_bytes.return_value = [b"hello"]
    mock_client.stream.return_value = stream_resp
    provider._client = mock_client

    result = json.loads(provider.get_attachment("10001"))

    assert result["filename"] == "error.log"
    assert base64.b64decode(result["content_base64"]) == b"hello"
    assert not list(tmp_path.iterdir())
    provider.close()


def test_attachment_size_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Attachment reads and downloads reject oversized attachments."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    meta_resp = MagicMock()
    meta_resp.status_code = 200
    meta_resp.json.return_value = {
        "id": "10001",
        "filename": "huge.zip",
        "size": _DEFAULT_MAX_ATTACHMENT_SIZE + 1,
        "mimeType": "application/zip",
        "content": "https://example.com/attachment/10001",
    }
    meta_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = meta_resp
    provider._client = mock_client

    result = json.loads(provider.get_attachment("10001"))
    assert "error" in result
    assert "exceeds limit" in result["error"]
    provider.close()


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def test_search_issues_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_issues returns error JSON on HTTP errors."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock()
    mock_client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500 Server Error",
        request=MagicMock(),
        response=MagicMock(status_code=500, text="Server Error"),
    )
    provider._client = mock_client

    result = json.loads(provider.search_issues("jql"))
    assert "error" in result
    assert result["error_code"] == "upstream_error"
    provider.close()


def test_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider handles connection errors gracefully."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")
    provider._client = mock_client

    result = json.loads(provider.search_issues("jql"))
    assert "error" in result
    provider.close()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_get_issue_invalid_key() -> None:
    """get_issue rejects invalid issue key formats."""
    provider = JiraProvider()
    result = provider.get_issue("../../../admin")
    data = json.loads(result)
    assert data["error_code"] == "invalid_issue_key"
    assert "Invalid issue key" in data["error"]

    result = provider.get_issue("123-BAD")
    assert json.loads(result)["error_code"] == "invalid_issue_key"
    provider.close()


def test_get_issue_valid_key_format() -> None:
    """get_issue accepts standard Jira key formats."""
    assert JiraProvider._validate_issue_key("PROJ-123") is None
    assert JiraProvider._validate_issue_key("A-1") is None
    assert JiraProvider._validate_issue_key("ABC-12345") is None
    assert JiraProvider._validate_issue_key("proj-123") is None


def test_get_attachment_invalid_id() -> None:
    """get_attachment rejects non-numeric attachment IDs."""
    provider = JiraProvider()
    result = json.loads(provider.get_attachment("abc"))
    assert "error" in result
    assert "Invalid attachment ID" in result["error"]
    provider.close()


def test_get_attachment_valid_id() -> None:
    """get_attachment accepts numeric IDs."""
    assert JiraProvider._validate_attachment_id("10001") is None
    assert JiraProvider._validate_attachment_id("0") is None


# ---------------------------------------------------------------------------
# 401 session expiry recovery
# ---------------------------------------------------------------------------


def test_search_issues_401_invalidates_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_issues discards the rejected session so a later call re-authenticates."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")

    provider = JiraProvider()
    mock_client = _make_mock_client()
    mock_client.get.return_value = MagicMock()
    mock_client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401, text="Unauthorized"),
    )
    provider._client = mock_client

    # The retry rebuilds a client, so keep login offline and let it fail the same way.
    with patch.object(JiraProvider, "_login", staticmethod(lambda *a: None)), patch(
        "server.providers.jira.httpx.Client", return_value=mock_client
    ):
        result = json.loads(provider.search_issues("jql"))
    assert "error" in result
    assert provider._client is None
    provider.close()


def test_expired_session_is_retried_transparently(tmp_path) -> None:
    """An idle-expired Jira session re-authenticates instead of failing the call."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="pass", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    request = httpx.Request("GET", "https://jira.example.com/rest/api/2/search")
    unauthorized = httpx.Response(401, request=request, json={"errorMessages": ["expired"]})
    success = MagicMock()
    success.raise_for_status = MagicMock()
    success.json.return_value = {"total": 0, "issues": []}

    clients: list[MagicMock] = []
    logins: list[int] = []

    def build_client(**_kwargs) -> MagicMock:
        client = _make_mock_client()
        # Only the first session is stale; the re-authenticated one succeeds.
        client.get.side_effect = lambda *a, **k: unauthorized if client is clients[0] else success
        clients.append(client)
        return client

    with (
        patch("server.providers.jira.httpx.Client", build_client),
        patch.object(JiraProvider, "_login", staticmethod(lambda *a: logins.append(1))),
    ):
        data = json.loads(provider.search_issues("project = TEST"))

    assert "error" not in data
    assert data["result_count"] == 0
    assert len(clients) == 2
    assert len(logins) == 2
    provider.close()


def test_persistent_401_stops_after_one_retry(tmp_path) -> None:
    """Genuinely rejected credentials surface an error instead of looping."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="bad", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    request = httpx.Request("GET", "https://jira.example.com/rest/api/2/search")
    unauthorized = httpx.Response(401, request=request, json={"errorMessages": ["nope"]})
    logins: list[int] = []

    def build_client(**_kwargs) -> MagicMock:
        client = _make_mock_client()
        client.get.return_value = unauthorized
        return client

    with (
        patch("server.providers.jira.httpx.Client", build_client),
        patch.object(JiraProvider, "_login", staticmethod(lambda *a: logins.append(1))),
    ):
        data = json.loads(provider.search_issues("project = TEST"))

    assert data["error_code"] == "authentication_error"
    assert len(logins) == 2
    assert provider._client is None
    provider.close()


def test_mutations_are_not_retried_on_401_by_default(tmp_path) -> None:
    """A gateway could rewrite a 401 after forwarding the write, so do not repeat it."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="pass", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    request = httpx.Request("POST", "https://jira.example.com/rest/api/2/issue/PROJ-1/comment")
    unauthorized = httpx.Response(401, request=request, json={"errorMessages": ["expired"]})
    posts: list[str] = []

    def build_client(**_kwargs) -> MagicMock:
        client = _make_mock_client()

        def post(url, **_kwargs):
            posts.append(url)
            return unauthorized

        client.post.side_effect = post
        return client

    with (
        patch("server.providers.jira.httpx.Client", build_client),
        patch.object(JiraProvider, "_login", staticmethod(lambda *a: None)),
    ):
        data = json.loads(provider.add_comment("PROJ-1", "Deployed"))

    assert data["error_code"] == "authentication_error"
    assert data["added"] is False
    # Exactly one attempt: no chance of a duplicate comment.
    assert len(posts) == 1
    # The rejected session is still discarded so the next call re-authenticates.
    assert provider._client is None
    provider.close()


def test_mutations_are_retried_on_401_when_explicitly_enabled(tmp_path) -> None:
    """Deployments that verified their edge can opt into write retries."""
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        output_dir=tmp_path,
        retry_mutations_on_401=True,
    )
    provider = JiraProvider(config)
    request = httpx.Request("POST", "https://jira.example.com/rest/api/2/issue/PROJ-1/comment")
    unauthorized = httpx.Response(401, request=request, json={"errorMessages": ["expired"]})
    created = MagicMock()
    created.raise_for_status = MagicMock()
    created.json.return_value = {"id": "10001", "body": "Deployed", "created": "now"}

    clients: list[MagicMock] = []
    posts: list[dict] = []

    def build_client(**_kwargs) -> MagicMock:
        client = _make_mock_client()

        def post(url, **kwargs):
            posts.append({"url": url, **kwargs})
            return unauthorized if client is clients[0] else created

        client.post.side_effect = post
        clients.append(client)
        return client

    with (
        patch("server.providers.jira.httpx.Client", build_client),
        patch.object(JiraProvider, "_login", staticmethod(lambda *a: None)),
    ):
        data = json.loads(provider.add_comment("PROJ-1", "Deployed"))

    assert data["added"] is True
    assert data["comment"]["id"] == "10001"
    # The rejected attempt plus exactly one retry: no duplicate comment.
    assert len(posts) == 2
    provider.close()


def test_attachment_download_recovers_from_an_expired_session(tmp_path) -> None:
    """Streamed downloads get the same session recovery as ordinary requests."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="pass", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    metadata = MagicMock()
    metadata.raise_for_status = MagicMock()
    metadata.status_code = 200
    metadata.json.return_value = {
        "filename": "file.txt",
        "size": 5,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/secure/attachment/1/file.txt",
    }
    request = httpx.Request("GET", "https://jira.example.com/secure/attachment/1/file.txt")
    unauthorized = httpx.Response(401, request=request, json={"errorMessages": ["expired"]})
    clients: list[MagicMock] = []
    streams: list[str] = []

    def build_client(**_kwargs) -> MagicMock:
        client = _make_mock_client()
        client.get.return_value = metadata

        def stream(method, url, **_kwargs):
            streams.append(url)
            if client is clients[0]:
                unauthorized.stream = MagicMock()
                context = MagicMock()
                context.__enter__ = MagicMock(return_value=unauthorized)
                context.__exit__ = MagicMock(return_value=False)
                return context
            body = MagicMock()
            body.__enter__ = MagicMock(return_value=body)
            body.__exit__ = MagicMock(return_value=False)
            body.raise_for_status = MagicMock()
            body.status_code = 200
            body.iter_bytes.return_value = [b"hello"]
            return body

        client.stream.side_effect = stream
        clients.append(client)
        return client

    with (
        patch("server.providers.jira.httpx.Client", build_client),
        patch.object(JiraProvider, "_login", staticmethod(lambda *a: None)),
    ):
        data = json.loads(provider.get_attachment("1"))

    assert "error" not in data
    assert base64.b64decode(data["content_base64"]) == b"hello"
    assert len(streams) == 2  # rejected attempt plus one retry
    provider.close()


def test_stale_401_does_not_discard_a_newer_session(tmp_path) -> None:
    """Invalidation targets the rejected client, not whatever is current."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="pass", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    rejected = _make_mock_client()
    current = _make_mock_client()
    provider._client = current

    provider._invalidate_client(rejected)

    assert provider._client is current
    current.close.assert_not_called()
    provider.close()


def test_jira_timeout_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid JIRA_TIMEOUT value falls back to default without crashing."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")
    monkeypatch.setenv("JIRA_TIMEOUT", "not-a-number")

    provider = JiraProvider()
    with patch.object(provider, "_login"):
        client = provider._get_client()
    assert client is not None
    provider.close()


def test_missing_configuration_returns_stable_error() -> None:
    data = json.loads(JiraProvider(JiraConfig()).search_issues("project = TEST"))
    assert data["error_code"] == "configuration_error"
    assert data["results"] == []


def test_context_path_is_preserved_in_client_base_url(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com/context",
        username="user",
        password="pass",
        output_dir=tmp_path,
    )
    provider = JiraProvider(config)
    with patch("server.providers.jira.httpx.Client") as client_class:
        client = client_class.return_value
        client.is_closed = False
        with patch.object(provider, "_login"):
            provider._get_client()
    assert client_class.call_args.kwargs["base_url"] == "https://jira.example.com/context/"


def test_attachment_rejects_cross_origin_download(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        output_dir=tmp_path,
    )
    provider = JiraProvider(config)
    client = _make_mock_client()
    metadata = MagicMock()
    metadata.raise_for_status = MagicMock()
    metadata.json.return_value = {
        "filename": "file.txt",
        "size": 4,
        "mimeType": "text/plain",
        "content": "https://evil.example/file.txt",
    }
    client.get.return_value = metadata
    provider._client = client
    data = json.loads(provider.download_attachment("10001", str(tmp_path / "file.txt")))
    assert data["error_code"] == "invalid_attachment_url"
    client.stream.assert_not_called()


def test_attachment_enforces_actual_stream_size_and_cleans_partial_file(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        max_attachment_size=5,
        output_dir=tmp_path,
    )
    provider = JiraProvider(config)
    client = _make_mock_client()
    metadata = MagicMock()
    metadata.raise_for_status = MagicMock()
    metadata.json.return_value = {
        "filename": "file.txt",
        "size": 4,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/file.txt",
    }
    client.get.return_value = metadata
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.__exit__.return_value = False
    stream.raise_for_status = MagicMock()
    stream.iter_bytes.return_value = [b"123", b"456"]
    client.stream.return_value = stream
    provider._client = client

    destination = tmp_path / "file.txt"
    data = json.loads(provider.download_attachment("10001", str(destination)))
    assert data["error_code"] == "attachment_too_large"
    assert not destination.exists()
    assert list(tmp_path.glob(".archon-jira-*.part")) == []


def test_client_is_not_published_before_login_completes(tmp_path) -> None:
    """A second thread must never receive a client that has no session cookie."""
    config = JiraConfig(
        url="https://jira.example.com", username="user", password="pass", output_dir=tmp_path
    )
    provider = JiraProvider(config)
    login_started = threading.Event()
    login_finished = threading.Event()
    observed: list[bool] = []

    def slow_login(client, username, password) -> None:
        login_started.set()
        time.sleep(0.3)
        login_finished.set()

    with (
        patch("server.providers.jira.httpx.Client", lambda **_kwargs: _make_mock_client()),
        patch.object(JiraProvider, "_login", staticmethod(slow_login)),
    ):
        thread = threading.Thread(target=provider._get_client)
        thread.start()
        assert login_started.wait(1.0)
        # While the first login is still in flight, any client handed out here
        # would be unauthenticated.
        provider._get_client()
        observed.append(login_finished.is_set())
        thread.join()

    assert observed == [True]
    provider.close()


def test_login_failure_discards_unusable_client(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="bad",
        output_dir=tmp_path,
    )
    provider = JiraProvider(config)
    request = httpx.Request("POST", "https://jira.example.com/rest/auth/1/session")
    response = httpx.Response(401, request=request, json={"message": "Unauthorized"})
    with patch("server.providers.jira.httpx.Client") as client_class:
        client = client_class.return_value
        client.is_closed = False
        client.post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized",
            request=request,
            response=response,
        )
        with pytest.raises(httpx.HTTPStatusError):
            provider._get_client()
    client.close.assert_called_once()
    assert provider._client is None


def test_http_error_extracts_structured_jira_message() -> None:
    provider = JiraProvider()
    client = _make_mock_client()
    request = httpx.Request("GET", "https://jira.example.com/rest/api/2/search")
    response = httpx.Response(
        400,
        request=request,
        json={"errorMessages": ["Invalid JQL field"]},
    )
    client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad request",
        request=request,
        response=response,
    )
    provider._client = client
    data = json.loads(provider.search_issues("bad jql"))
    assert data["error_code"] == "invalid_jql"
    assert "Invalid JQL field" in data["error"]


def test_invalid_jql_marks_metadata_cache_stale() -> None:
    provider = JiraProvider()
    provider._jql_cache.invalidate = MagicMock()
    client = _make_mock_client()
    request = httpx.Request("GET", "https://jira.example.com/rest/api/2/search")
    response = httpx.Response(400, request=request, json={"errorMessages": ["Bad field"]})
    client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad request", request=request, response=response
    )
    provider._client = client

    provider.search_issues("bad jql")

    provider._jql_cache.invalidate.assert_called_once_with()


def test_search_jql_fields_merges_field_and_autocomplete_metadata(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        jql_disk_cache_enabled=False,
        jql_cache_dir=tmp_path,
    )
    provider = JiraProvider(config)
    client = _make_mock_client()
    fields_response = MagicMock()
    fields_response.raise_for_status = MagicMock()
    fields_response.json.return_value = [
        {
            "id": "customfield_11122",
            "name": "测试阶段",
            "custom": True,
            "searchable": True,
            "orderable": True,
            "clauseNames": ["测试阶段"],
            "schema": {"type": "option"},
        }
    ]
    autocomplete_response = MagicMock()
    autocomplete_response.raise_for_status = MagicMock()
    autocomplete_response.json.return_value = {
        "visibleFieldNames": [
            {
                "value": "测试阶段",
                "displayName": "测试阶段",
                "cfid": "customfield_11122",
                "operators": ["=", "IN"],
                "types": ["java.lang.String"],
                "searchable": "true",
            }
        ]
    }
    client.get.side_effect = [fields_response, autocomplete_response]
    provider._client = client

    data = json.loads(provider.search_jql_fields("测试"))

    assert data["result_count"] == 1
    assert data["fields"][0]["id"] == "customfield_11122"
    assert data["fields"][0]["schema_type"] == "option"
    assert data["fields"][0]["operators"] == ["=", "IN"]
    assert "cf[11122]" in data["fields"][0]["clause_names"]
    assert data["fields"][0]["jql_clause"] == "cf[11122]"
    assert data["cache"]["source"] == "jira"

    cached = json.loads(provider.search_jql_fields("测试"))
    assert cached["cache"]["source"] == "memory"
    assert client.get.call_count == 2


def test_get_jql_value_suggestions_uses_resolved_clause_and_cache(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        jql_disk_cache_enabled=False,
        jql_cache_dir=tmp_path,
    )
    provider = JiraProvider(config)
    client = _make_mock_client()
    fields_response = MagicMock()
    fields_response.raise_for_status = MagicMock()
    fields_response.json.return_value = [
        {
            "id": "customfield_11122",
            "name": "测试阶段",
            "custom": True,
            "searchable": True,
            "clauseNames": ["测试阶段"],
            "schema": {"type": "option"},
        }
    ]
    autocomplete_response = MagicMock()
    autocomplete_response.raise_for_status = MagicMock()
    autocomplete_response.json.return_value = {"visibleFieldNames": []}
    suggestions_response = MagicMock()
    suggestions_response.raise_for_status = MagicMock()
    suggestions_response.json.return_value = {
        "results": [{"value": "待回归", "displayName": "待回归"}]
    }
    client.get.side_effect = [fields_response, autocomplete_response, suggestions_response]
    provider._client = client

    data = json.loads(provider.get_jql_value_suggestions("测试阶段", "回归"))

    assert data["field"]["jql_clause"] == "cf[11122]"
    assert data["suggestions"][0]["jql_literal"] == '"待回归"'
    assert client.get.call_args.kwargs["params"] == {
        "fieldName": "cf[11122]",
        "fieldValue": "回归",
    }

    cached = json.loads(provider.get_jql_value_suggestions("测试阶段", "回归"))
    assert cached["cache"]["source"] == "memory"
    assert client.get.call_count == 3


def test_get_jql_value_suggestions_reports_unknown_field(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        jql_disk_cache_enabled=False,
        jql_cache_dir=tmp_path,
    )
    provider = JiraProvider(config)
    provider._jql_cache.get_fields = MagicMock(
        return_value=MagicMock(
            data={"fields": []},
            metadata=lambda: {"source": "memory", "stale": False},
        )
    )

    data = json.loads(provider.get_jql_value_suggestions("不存在"))

    assert data["error_code"] == "unknown_jql_field"
    assert data["suggestions"] == []


def test_get_jql_value_suggestions_reports_unsupported_endpoint(tmp_path) -> None:
    config = JiraConfig(
        url="https://jira.example.com",
        username="user",
        password="pass",
        jql_disk_cache_enabled=False,
        jql_cache_dir=tmp_path,
    )
    provider = JiraProvider(config)
    field = {
        "id": "status",
        "name": "Status",
        "clause_names": ["status"],
        "schema_type": "status",
    }
    provider._jql_cache.get_fields = MagicMock(
        return_value=MagicMock(
            data={"fields": [field]},
            metadata=lambda: {"source": "memory", "stale": False},
        )
    )
    client = _make_mock_client()
    request = httpx.Request(
        "GET",
        "https://jira.example.com/rest/api/2/jql/autocompletedata/suggestions",
    )
    response = httpx.Response(404, request=request)
    client.get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not found", request=request, response=response
    )
    provider._client = client

    data = json.loads(provider.get_jql_value_suggestions("status"))

    assert data["error_code"] == "metadata_unsupported"
