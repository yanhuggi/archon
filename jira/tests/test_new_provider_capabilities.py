"""Provider tests for identity, permissions, changelog, and issue creation."""

import json
import threading
from unittest.mock import MagicMock

import httpx

from server.config import JiraConfig
from server.providers.jira import JiraProvider


def response(payload: object) -> MagicMock:
    result = MagicMock()
    result.json.return_value = payload
    return result


def not_found(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    result = httpx.Response(404, request=request, json={"errorMessages": ["Not found"]})
    return httpx.HTTPStatusError("Not found", request=request, response=result)


def provider() -> JiraProvider:
    return JiraProvider(
        JiraConfig(url="https://jira.example.com", username="user", password="pass")
    )


def test_get_current_user_falls_back_for_older_jira() -> None:
    jira = provider()
    jira._request = MagicMock(side_effect=[
        not_found("https://jira.example.com/rest/api/2/myself"),
        response({
            "name": "user",
            "key": "user-key",
            "displayName": "User Name",
            "emailAddress": "user@example.com",
            "active": True,
            "timeZone": "Asia/Shanghai",
            "groups": {"size": 1, "items": [{"name": "developers"}]},
        }),
    ])

    data = json.loads(jira.get_current_user())

    assert data["authenticated"] is True
    assert data["user"] == {
        "name": "user",
        "key": "user-key",
        "display_name": "User Name",
        "email_address": "user@example.com",
        "active": True,
        "time_zone": "Asia/Shanghai",
        "groups": ["developers"],
        "group_count": 1,
        "groups_truncated": False,
    }
    assert jira._request.call_args_list[1].kwargs["params"] == {"username": "user"}


def test_get_my_permissions_filters_exact_instance_keys() -> None:
    jira = provider()
    jira._request = MagicMock(return_value=response({
        "permissions": {
            "DELETE_ISSUE": {
                "id": "16",
                "key": "DELETE_ISSUE",
                "name": "Delete Issues",
                "havePermission": False,
            },
            "EDIT_ISSUE": {
                "id": "12",
                "key": "EDIT_ISSUE",
                "name": "Edit Issues",
                "havePermission": True,
            },
        }
    }))

    data = json.loads(
        jira.get_my_permissions(
            issue_key="SAAS-2762",
            permissions=["DELETE_ISSUE", "missing"],
        )
    )

    assert data["context"] == {"scope": "issue", "issue_key": "SAAS-2762"}
    assert data["permission_count"] == 1
    assert data["permissions"][0]["key"] == "DELETE_ISSUE"
    assert data["permissions"][0]["have_permission"] is False
    assert data["unknown_permissions"] == ["missing"]
    assert jira._request.call_args.kwargs["params"] == {"issueKey": "SAAS-2762"}


def test_get_issue_changelog_uses_paginated_endpoint() -> None:
    jira = provider()
    jira._request = MagicMock(return_value=response({
        "startAt": 5,
        "maxResults": 10,
        "total": 7,
        "histories": [{
            "id": "100",
            "author": {"name": "user", "displayName": "User"},
            "created": "2026-08-14T10:00:00.000+0800",
            "items": [{
                "field": "status",
                "fieldId": "status",
                "fieldtype": "jira",
                "from": "1",
                "fromString": "Open",
                "to": "3",
                "toString": "In Progress",
            }],
        }],
    }))

    data = json.loads(jira.get_issue_changelog("SAAS-2762", max_results=10, start_at=5))

    assert data["source"] == "endpoint"
    assert data["history_count"] == 1
    assert data["histories"][0]["items"][0]["to_string"] == "In Progress"
    assert data["has_more"] is True
    assert data["next_start_at"] == 6


def test_get_issue_changelog_reports_embedded_history_limit() -> None:
    jira = provider()
    jira._request = MagicMock(side_effect=[
        not_found("https://jira.example.com/rest/api/2/issue/SAAS-2762/changelog"),
        response({
            "key": "SAAS-2762",
            "changelog": {
                "startAt": 0,
                "maxResults": 2,
                "total": 3,
                "histories": [
                    {"id": "1", "items": []},
                    {"id": "2", "items": []},
                ],
            },
        }),
    ])

    data = json.loads(jira.get_issue_changelog("SAAS-2762", max_results=1, start_at=1))

    assert data["source"] == "embedded"
    assert data["histories"][0]["id"] == "2"
    assert data["pagination_supported"] is False
    assert data["has_more"] is True
    assert data["next_start_at"] is None
    assert "warning" in data


def create_metadata(*, required_summary: bool = True) -> dict:
    return {
        "projects": [{
            "id": "10000",
            "key": "SAAS",
            "issuetypes": [{
                "id": "3",
                "name": "Task",
                "fields": {
                    "project": {"required": True, "operations": ["set"]},
                    "issuetype": {"required": True, "operations": []},
                    "summary": {
                        "name": "Summary",
                        "required": required_summary,
                        "operations": ["set"],
                        "schema": {"type": "string", "system": "summary"},
                    },
                    "description": {
                        "name": "Description",
                        "required": False,
                        "operations": ["set"],
                        "schema": {"type": "string", "system": "description"},
                    },
                    "attachment": {
                        "name": "Attachment",
                        "required": False,
                        "operations": [],
                    },
                },
            }],
        }]
    }


def test_create_issue_validates_metadata_before_posting() -> None:
    jira = provider()
    jira._request = MagicMock(side_effect=[
        response(create_metadata()),
        response({"id": "20001", "key": "SAAS-42"}),
    ])

    data = json.loads(
        jira.create_issue(
            "SAAS",
            "Task",
            {"summary": "New issue", "description": "Details"},
        )
    )

    assert data["created"] is True
    assert data["issue_key"] == "SAAS-42"
    assert jira._request.call_count == 2
    create_call = jira._request.call_args_list[1]
    assert create_call.args == ("POST", "rest/api/2/issue")
    assert create_call.kwargs["json"] == {
        "fields": {
            "project": {"key": "SAAS"},
            "issuetype": {"id": "3"},
            "summary": "New issue",
            "description": "Details",
        }
    }


def test_create_issue_refuses_missing_or_uncreatable_fields_without_post() -> None:
    missing = provider()
    missing._request = MagicMock(return_value=response(create_metadata()))
    missing_data = json.loads(missing.create_issue("SAAS", "Task", {"description": "x"}))

    uncreatable = provider()
    uncreatable._request = MagicMock(return_value=response(create_metadata()))
    uncreatable_data = json.loads(
        uncreatable.create_issue("SAAS", "Task", {"summary": "x", "attachment": []})
    )

    assert missing_data["error_code"] == "missing_required_fields"
    assert missing_data["required_fields"][0]["id"] == "summary"
    assert missing._request.call_count == 1
    assert uncreatable_data["error_code"] == "uncreatable_fields"
    assert uncreatable_data["uncreatable_fields"] == ["attachment"]
    assert uncreatable._request.call_count == 1


def test_create_issue_rejects_alias_collisions_without_post() -> None:
    jira = provider()
    metadata = create_metadata()
    metadata["projects"][0]["issuetypes"][0]["fields"]["summary"]["key"] = "title"
    jira._request = MagicMock(return_value=response(metadata))

    data = json.loads(
        jira.create_issue("SAAS", "Task", {"summary": "one", "title": "two"})
    )

    assert data["error_code"] == "duplicate_fields"
    assert data["duplicate_fields"] == [["summary", "title"]]
    assert jira._request.call_count == 1


def test_create_issue_replays_recent_identical_request() -> None:
    jira = provider()
    jira._create_issue_once = MagicMock(return_value=json.dumps({
        "project_key": "SAAS",
        "issue_key": "SAAS-42",
        "created": True,
    }))

    first = json.loads(jira.create_issue("SAAS", "Task", {"summary": "Same"}))
    second = json.loads(jira.create_issue("SAAS", "Task", {"summary": "Same"}))

    assert first["issue_key"] == "SAAS-42"
    assert "deduplicated" not in first
    assert second["issue_key"] == "SAAS-42"
    assert second["deduplicated"] is True
    assert jira._create_issue_once.call_count == 1


def test_create_issue_coalesces_concurrent_identical_requests() -> None:
    jira = provider()
    create_started = threading.Event()
    release_create = threading.Event()
    waiter_started = threading.Event()
    calls = 0

    def create_once(*_args, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        create_started.set()
        assert release_create.wait(2)
        return json.dumps({
            "project_key": "SAAS",
            "issue_key": "SAAS-42",
            "created": True,
        })

    original_wait = jira._wait_for_create_dedupe

    def wait_for_result(token, event, fingerprint) -> str:
        waiter_started.set()
        return original_wait(token, event, fingerprint)

    jira._create_issue_once = create_once
    jira._wait_for_create_dedupe = wait_for_result
    results: list[dict] = []

    def create() -> None:
        results.append(json.loads(jira.create_issue("SAAS", "Task", {"summary": "Same"})))

    first = threading.Thread(target=create)
    second = threading.Thread(target=create)
    first.start()
    assert create_started.wait(1)
    second.start()
    assert waiter_started.wait(1)
    release_create.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == 1
    assert {result["issue_key"] for result in results} == {"SAAS-42"}
    assert sum(result.get("deduplicated") is True for result in results) == 1


def test_create_issue_rejects_idempotency_key_reuse_with_different_data() -> None:
    jira = provider()
    jira._create_issue_once = MagicMock(return_value=json.dumps({
        "project_key": "SAAS",
        "issue_key": "SAAS-42",
        "created": True,
    }))

    first = json.loads(
        jira.create_issue(
            "SAAS",
            "Task",
            {"summary": "First"},
            idempotency_key="submission-123",
        )
    )
    conflicting = json.loads(
        jira.create_issue(
            "SAAS",
            "Task",
            {"summary": "Different"},
            idempotency_key="submission-123",
        )
    )

    assert first["created"] is True
    assert conflicting["error_code"] == "idempotency_key_reused"
    assert jira._create_issue_once.call_count == 1
