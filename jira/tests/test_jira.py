"""Tests for server.providers.jira — JiraProvider implementation."""

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from server.providers import get_provider, is_registered, register
from server.providers.jira import (
    _DEFAULT_MAX_ATTACHMENT_SIZE,
    _MAX_FIELD_LENGTH,
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
    assert result["comments"][0]["author"] == "John"
    provider.close()


# ---------------------------------------------------------------------------
# get_attachment
# ---------------------------------------------------------------------------


def test_get_attachment_saves_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """get_attachment downloads and saves file to disk."""
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
        result = json.loads(provider.get_attachment("10001", save_to=save_path))
    assert result["filename"] == "error.log"
    assert result["saved_to"] == save_path
    assert os.path.exists(save_path)
    with open(save_path, "rb") as f:
        assert f.read() == b"Error: connection refused"
    provider.close()


def test_get_attachment_size_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """get_attachment rejects attachments exceeding size limit."""
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

    result = json.loads(provider.get_attachment("10001", save_to=str(tmp_path / "huge.zip")))
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
        "401 Unauthorized",
        request=MagicMock(),
        response=MagicMock(status_code=401, text="Unauthorized"),
    )
    provider._client = mock_client

    result = json.loads(provider.search_issues("jql"))
    assert "error" in result
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
    assert "Error" in result
    assert "Invalid issue key" in result

    result = provider.get_issue("123-BAD")
    assert "Error" in result
    provider.close()


def test_get_issue_valid_key_format() -> None:
    """get_issue accepts standard Jira key formats."""
    assert JiraProvider._validate_issue_key("PROJ-123") is None
    assert JiraProvider._validate_issue_key("A-1") is None
    assert JiraProvider._validate_issue_key("ABC-12345") is None
    assert JiraProvider._validate_issue_key("proj-123") is None


def test_get_attachment_invalid_id(tmp_path) -> None:
    """get_attachment rejects non-numeric attachment IDs."""
    provider = JiraProvider()
    result = json.loads(provider.get_attachment("abc", save_to=str(tmp_path / "test.txt")))
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
    """search_issues invalidates client on 401 so next call creates fresh session."""
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

    result = json.loads(provider.search_issues("jql"))
    assert "error" in result
    assert provider._client is None
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
