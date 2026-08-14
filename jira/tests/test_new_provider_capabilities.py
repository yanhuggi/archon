"""Provider tests for identity, permissions, changelog, and issue creation."""

import json
import threading
from unittest.mock import MagicMock, patch

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


def test_get_issue_changelog_uses_server_dc_embedded_contract() -> None:
    jira = provider()
    jira._request = MagicMock(return_value=response({
        "key": "SAAS-2762",
        "changelog": {
            "startAt": 0,
            "maxResults": 2,
            "total": 2,
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
            }, {"id": "101", "items": []}],
        },
    }))

    data = json.loads(jira.get_issue_changelog("SAAS-2762", max_results=1, start_at=0))

    assert data["source"] == "embedded"
    assert data["history_count"] == 1
    assert data["histories"][0]["items"][0]["to_string"] == "In Progress"
    assert data["has_more"] is True
    assert data["next_start_at"] == 1
    assert data["pagination_supported"] is True
    assert jira._request.call_args.args == ("GET", "rest/api/2/issue/SAAS-2762")
    assert jira._request.call_args.kwargs["params"] == {
        "fields": "summary",
        "expand": "changelog",
    }


def test_get_issue_changelog_reports_embedded_history_limit() -> None:
    jira = provider()
    jira._request = MagicMock(return_value=response({
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
    }))

    data = json.loads(jira.get_issue_changelog("SAAS-2762", max_results=1, start_at=0))

    assert data["source"] == "embedded"
    assert data["histories"][0]["id"] == "1"
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


def test_unknown_create_outcome_keeps_explicit_key_blocked() -> None:
    jira = provider()
    request = httpx.Request("POST", "https://jira.example.com/rest/api/2/issue")
    timeout = httpx.ReadTimeout("response was lost", request=request)
    jira._request = MagicMock(side_effect=[response(create_metadata()), timeout])
    clock = {"now": 100.0}

    with patch("server.providers.jira.time.monotonic", side_effect=lambda: clock["now"]):
        first = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"summary": "Possibly created"},
                idempotency_key="submission-unknown",
            )
        )
        clock["now"] += 11.0
        replay = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"summary": "Possibly created"},
                idempotency_key="submission-unknown",
            )
        )

    assert first["error_code"] == "create_outcome_unknown"
    assert first["created"] is None
    assert first["outcome_unknown"] is True
    assert first["retry_blocked"] is True
    assert first["upstream_error_code"] == "upstream_error"
    assert replay["error_code"] == "create_outcome_unknown"
    assert replay["deduplicated"] is True
    assert jira._request.call_count == 2


def test_unparseable_create_response_is_an_unknown_outcome() -> None:
    jira = provider()
    create_response = response({})
    create_response.json.side_effect = ValueError("invalid JSON")
    jira._request = MagicMock(side_effect=[response(create_metadata()), create_response])

    data = json.loads(
        jira.create_issue(
            "SAAS",
            "Task",
            {"summary": "Possibly created"},
            idempotency_key="submission-invalid-response",
        )
    )

    assert data["error_code"] == "create_outcome_unknown"
    assert data["upstream_error_code"] == "provider_error"
    assert data["retry_blocked"] is True


def test_pre_submit_create_failure_is_only_cached_briefly() -> None:
    jira = provider()
    jira._request = MagicMock(return_value=response(create_metadata()))
    clock = {"now": 100.0}

    with patch("server.providers.jira.time.monotonic", side_effect=lambda: clock["now"]):
        first = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"description": "Missing summary"},
                idempotency_key="submission-invalid",
            )
        )
        clock["now"] += 11.0
        second = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"description": "Missing summary"},
                idempotency_key="submission-invalid",
            )
        )

    assert first["error_code"] == "missing_required_fields"
    assert second["error_code"] == "missing_required_fields"
    assert "deduplicated" not in second
    assert jira._request.call_count == 2


def test_rejected_create_is_only_cached_briefly() -> None:
    jira = provider()
    request = httpx.Request("POST", "https://jira.example.com/rest/api/2/issue")
    bad_request = httpx.Response(
        400,
        request=request,
        json={"errorMessages": ["Summary is required"]},
    )
    rejection = httpx.HTTPStatusError(
        "Bad request", request=request, response=bad_request
    )
    jira._request = MagicMock(
        side_effect=[
            response(create_metadata()),
            rejection,
            response(create_metadata()),
            rejection,
        ]
    )
    clock = {"now": 100.0}

    with patch("server.providers.jira.time.monotonic", side_effect=lambda: clock["now"]):
        first = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"summary": "Rejected"},
                idempotency_key="submission-rejected",
            )
        )
        clock["now"] += 11.0
        second = json.loads(
            jira.create_issue(
                "SAAS",
                "Task",
                {"summary": "Rejected"},
                idempotency_key="submission-rejected",
            )
        )

    assert first["error_code"] == "invalid_fields"
    assert first["created"] is False
    assert first.get("outcome_unknown") is not True
    assert second["error_code"] == "invalid_fields"
    assert second["created"] is False
    assert second.get("deduplicated") is not True
    assert jira._request.call_count == 4
