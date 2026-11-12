"""Native clone behavior, mutation boundaries, and the public MCP contract."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
from mcp.server import MCPServer
from server.config import JiraConfig
from server.providers.clone import ClonePage
from server.providers.jira import JiraProvider
from server.tools.clone_issue import register as register_tool

FORM = """<form action="QuickSearch.jspa" method="post"><input name="id" value="wrong"></form>
<form id="assign-issue" action="CloneIssueDetails.jspa" method="post">
<input type="hidden" name="id" value="12345">
<input type="text" name="summary" value="复制 - 原标题 &amp; 说明">
<input type="checkbox" name="cloneAttachments" value="true">
<input type="checkbox" name="cloneSubTasks" value="true">
<input type="checkbox" name="cloneLinks" value="true">
<input type="hidden" name="atl_token" value="csrf-secret">
<input type="submit" name="Create" value="创建">
</form>"""
SOURCE = {
    "id": "12345",
    "key": "APP-1",
    "fields": {
        "attachment": [{"id": "200"}],
        "subtasks": [{"key": "APP-2"}],
        "issuelinks": [{"id": "300"}],
    },
}


@pytest.fixture
def native_jira():
    instances = []

    def build(
        *, form=FORM, post=None, progress=None, source=SOURCE, retry_mutations=False
    ):
        requests = []

        def handler(request):
            requests.append(request)
            if (
                request.method == "GET"
                and request.url.path == "/jira/rest/api/2/issue/APP-1"
            ):
                return httpx.Response(200, json=source)
            if (
                request.method == "GET"
                and request.url.path == "/jira/secure/CloneIssueDetails!default.jspa"
            ):
                return httpx.Response(200, text=form)
            if (
                request.method == "POST"
                and request.url.path == "/jira/secure/CloneIssueDetails.jspa"
            ):
                if isinstance(post, Exception):
                    raise post
                if callable(post):
                    return post(request)
                return (
                    post
                    if post is not None
                    else httpx.Response(302, headers={"Location": "/jira/browse/APP-9"})
                )
            if (
                request.method == "GET"
                and request.url.path == "/jira/secure/CloneIssueDetails!progress.jspa"
            ):
                return (
                    progress(request)
                    if progress
                    else httpx.Response(303, headers={"Location": "/jira/browse/APP-9"})
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        provider = JiraProvider(
            JiraConfig(
                url="https://jira.example.com/jira",
                username="user",
                password="pass",
                retry_mutations_on_401=retry_mutations,
            )
        )
        provider._client = httpx.Client(
            base_url="https://jira.example.com/jira/",
            transport=httpx.MockTransport(handler),
        )
        instances.append(provider)
        return provider, requests

    yield build
    for instance in instances:
        instance.close()


def test_native_clone_submits_csrf_and_keeps_jiras_default_title(native_jira):
    provider, requests = native_jira()

    result = json.loads(provider.clone_issue(" app-1 "))

    assert result["cloned"] is True
    assert result["created"] is True
    assert result["source_issue_key"] == "APP-1"
    assert result["issue_key"] == "APP-9"
    assert result["url"] == "https://jira.example.com/jira/browse/APP-9"
    assert result["summary"] == "复制 - 原标题 & 说明"
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
    assert requests[1].url.params["id"] == "12345"
    submitted = parse_qs(requests[-1].content.decode())
    assert submitted == {
        "id": ["12345"],
        "summary": ["复制 - 原标题 & 说明"],
        "atl_token": ["csrf-secret"],
        "Create": ["创建"],
        "cloneAttachments": ["false"],
        "cloneSubTasks": ["false"],
        "cloneLinks": ["false"],
    }
    assert "csrf-secret" not in json.dumps(result)


def test_native_clone_custom_title_and_copy_options(native_jira):
    provider, requests = native_jira()

    result = json.loads(
        provider.clone_issue(
            "APP-1",
            summary=" 第二阶段 ",
            clone_attachments=True,
            clone_subtasks=True,
            clone_links=True,
        )
    )

    assert result["summary"] == "第二阶段"
    submitted = parse_qs(requests[-1].content.decode())
    assert submitted["summary"] == ["第二阶段"]
    assert all(
        submitted[name] == ["true"]
        for name in ("cloneAttachments", "cloneSubTasks", "cloneLinks")
    )


@pytest.mark.parametrize(
    "form",
    [
        "<html>Login required</html>",
        FORM.replace('name="atl_token"', 'name="other_token"'),
        FORM.replace('value="12345"', 'value="99999"'),
        FORM.replace('method="post"', 'method="get"'),
        FORM.replace('<input type="hidden" name="atl_token" value="csrf-secret">', ""),
    ],
)
def test_missing_or_invalid_native_form_never_submits(native_jira, form):
    provider, requests = native_jira(form=form)
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["error_code"] == "clone_unavailable"
    assert result["created"] is False
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize(
    "option,argument",
    [
        ("cloneAttachments", "clone_attachments"),
        ("cloneSubTasks", "clone_subtasks"),
        ("cloneLinks", "clone_links"),
    ],
)
def test_unavailable_requested_option_never_submits(native_jira, option, argument):
    form = FORM.replace(f'name="{option}"', 'name="unavailable"')
    provider, requests = native_jira(form=form)
    result = json.loads(provider.clone_issue("APP-1", **{argument: True}))
    assert result["error_code"] == "clone_options_unavailable"
    assert result["unavailable_options"] == [option]
    assert all(request.method == "GET" for request in requests)


def test_missing_options_with_no_source_content_are_noops(native_jira):
    provider, requests = native_jira(
        form=FORM.replace('type="checkbox"', 'type="hidden"'),
        source={
            "id": "12345",
            "key": "APP-1",
            "fields": {"attachment": [], "subtasks": [], "issuelinks": []},
        },
    )
    result = json.loads(
        provider.clone_issue(
            "APP-1", clone_attachments=True, clone_subtasks=True, clone_links=True
        )
    )
    assert result["cloned"] is True
    assert "cloneAttachments" not in parse_qs(requests[-1].content.decode())


def test_disabled_option_with_content_is_refused(native_jira):
    provider, requests = native_jira(
        form=FORM.replace('name="cloneAttachments"', 'name="cloneAttachments" disabled')
    )
    result = json.loads(provider.clone_issue("APP-1", clone_attachments=True))
    assert result["error_code"] == "clone_options_unavailable"
    assert all(request.method == "GET" for request in requests)


def test_native_form_error_cannot_rule_out_a_partial_clone(native_jira):
    response = httpx.Response(
        200,
        text=FORM.replace(
            "</form>",
            '<div class="error">Cannot clone <strong>this issue</strong>.</div></form>',
        ),
    )
    provider, requests = native_jira(post=response)
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["error_code"] == "clone_outcome_unknown"
    assert result["upstream_error_code"] == "clone_reported_error"
    assert "Cannot clone" in result["upstream_error"]
    assert result["cloned"] is None
    assert result["outcome_unknown"] is True
    assert len(requests) == 3


def test_native_async_progress_then_browse_redirect(native_jira):
    provider, requests = native_jira(
        post=httpx.Response(
            302, headers={"Location": "CloneIssueDetails!progress.jspa?taskId=7"}
        )
    )
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["cloned"] is True
    assert requests[-1].method == "GET"
    assert requests[-1].url.params["taskId"] == "7"
    assert len(requests) == 4


@pytest.mark.parametrize("header", [False, True])
def test_native_progress_refresh_is_polled_without_reposting(
    native_jira, monkeypatch, header
):
    monkeypatch.setattr("server.providers.clone.time.sleep", lambda _: None)
    count = 0

    def progress(request):
        nonlocal count
        count += 1
        if count == 1:
            if header:
                return httpx.Response(
                    200,
                    headers={
                        "Refresh": '1; url="CloneIssueDetails!progress.jspa?taskId=7"'
                    },
                )
            return httpx.Response(
                200,
                text='<meta http-equiv="refresh" content="1; url=CloneIssueDetails!progress.jspa?taskId=7">',
            )
        return httpx.Response(302, headers={"Location": "/jira/browse/APP-9"})

    provider, requests = native_jira(
        post=httpx.Response(
            302, headers={"Location": "CloneIssueDetails!progress.jspa?taskId=7"}
        ),
        progress=progress,
    )
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["cloned"] is True
    assert count == 2
    assert sum(request.method == "POST" for request in requests) == 1


def test_unfinished_progress_is_unknown_and_retries_do_not_repost(
    native_jira, monkeypatch
):
    monkeypatch.setattr("server.providers.clone._MAX_PROGRESS_POLLS", 1)
    provider, requests = native_jira(
        post=httpx.Response(
            302, headers={"Location": "CloneIssueDetails!progress.jspa?taskId=7"}
        ),
        progress=lambda request: httpx.Response(200, text="Still cloning"),
    )
    first = json.loads(provider.clone_issue("APP-1", idempotency_key="submission"))
    second = json.loads(provider.clone_issue("APP-1", idempotency_key="submission"))
    assert first["error_code"] == "clone_outcome_unknown"
    assert first["upstream_error_code"] == "clone_in_progress"
    assert (
        first["progress_url"]
        == "https://jira.example.com/jira/secure/CloneIssueDetails!progress.jspa?taskId=7"
    )
    assert second["deduplicated"] is True
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.parametrize(
    "post",
    [
        httpx.ReadTimeout("timed out"),
        httpx.Response(500, json={"errorMessages": ["Failed"]}),
        httpx.Response(200, text="Unrecognized success response"),
        httpx.Response(302, headers={"Location": "/jira/browse/APP-1"}),
    ],
)
def test_unknown_post_outcome_stays_blocked_for_implicit_retries(native_jira, post):
    provider, requests = native_jira(post=post)
    first = json.loads(provider.clone_issue("APP-1"))
    second = json.loads(provider.clone_issue("APP-1"))
    assert first["error_code"] == "clone_outcome_unknown"
    assert first["created"] is None
    assert first["cloned"] is None
    assert first["outcome_unknown"] is True
    assert first["retry_blocked"] is True
    assert second["deduplicated"] is True
    assert sum(request.method == "POST" for request in requests) == 1
    assert (
        next(iter(provider._create_dedupe_cache.values()))[0] - time.monotonic()
        > 23 * 60 * 60
    )


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/jira/browse/APP-9",
        "//evil.example/jira/browse/APP-9",
        "http://jira.example.com/jira/browse/APP-9",
        "https://user@jira.example.com/jira/browse/APP-9",
        "/other/browse/APP-9",
        "/jira/secure/DeleteIssue.jspa?id=12345",
        "/jira/secure/%2e%2e/DeleteIssue.jspa",
        "/jira/browse/APP-9/extra",
        "/login.jsp",
    ],
)
def test_unexpected_redirects_are_not_followed(native_jira, location):
    provider, requests = native_jira(
        post=httpx.Response(302, headers={"Location": location})
    )
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["error_code"] == "clone_outcome_unknown"
    assert result["upstream_error_code"] == "invalid_clone_redirect"
    assert len(requests) == 3


@pytest.mark.parametrize("status", [400, 401, 403])
def test_rejected_post_is_not_retried_even_with_mutation_retry_enabled(
    native_jira, status
):
    provider, requests = native_jira(
        post=httpx.Response(status, json={"errorMessages": ["Denied"]}),
        retry_mutations=True,
    )
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["created"] is False
    assert not result.get("outcome_unknown")
    assert sum(request.method == "POST" for request in requests) == 1


def test_success_replays_and_conflicting_key_is_rejected(native_jira):
    provider, requests = native_jira()
    first = json.loads(provider.clone_issue("APP-1", idempotency_key="submission"))
    second = json.loads(provider.clone_issue("APP-1", idempotency_key="submission"))
    conflicting = json.loads(
        provider.clone_issue("APP-1", clone_links=True, idempotency_key="submission")
    )
    assert first["issue_key"] == second["issue_key"]
    assert second["deduplicated"] is True
    assert conflicting["error_code"] == "idempotency_key_reused"
    assert sum(request.method == "POST" for request in requests) == 1


def test_concurrent_duplicate_clone_submits_once(native_jira, monkeypatch):
    posted, waiting, release = threading.Event(), threading.Event(), threading.Event()

    def post(request):
        posted.set()
        assert release.wait(5)
        return httpx.Response(302, headers={"Location": "/jira/browse/APP-9"})

    provider, requests = native_jira(post=post)
    original_wait = provider._wait_for_create_dedupe

    def wait(*args):
        waiting.set()
        return original_wait(*args)

    monkeypatch.setattr(provider, "_wait_for_create_dedupe", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provider.clone_issue, "APP-1", idempotency_key="submission")
        try:
            assert posted.wait(5)
            second = pool.submit(
                provider.clone_issue, "APP-1", idempotency_key="submission"
            )
            assert waiting.wait(5)
        finally:
            release.set()
        results = [json.loads(future.result(timeout=5)) for future in (first, second)]
    assert all(result["cloned"] is True for result in results)
    assert sum(result.get("deduplicated", False) for result in results) == 1
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.parametrize(
    "source", [None, [], {"id": "bad", "fields": {}}, {"id": "12345", "fields": []}]
)
def test_invalid_source_response_never_submits(native_jira, source):
    provider, requests = native_jira(source=source)
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["error_code"] == "invalid_provider_response"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"source_issue_key": "bad key"}, "invalid_issue_key"),
        ({"summary": " "}, "invalid_summary"),
        ({"summary": "x" * 256}, "invalid_summary"),
        ({"summary": "bad\nsummary"}, "invalid_summary"),
        ({"summary": 12}, "invalid_summary"),
        ({"clone_links": "true"}, "invalid_clone_options"),
        ({"idempotency_key": " "}, "invalid_idempotency_key"),
        ({"idempotency_key": "bad\nkey"}, "invalid_idempotency_key"),
        ({"idempotency_key": "x" * 201}, "invalid_idempotency_key"),
    ],
)
def test_provider_and_tool_validation_do_not_call_jira(native_jira, kwargs, code):
    provider, requests = native_jira()
    server = MCPServer("test")
    register_tool(server, provider=provider)
    fn = server._tool_manager.get_tool("clone_issue").fn
    arguments = {"source_issue_key": "APP-1", **kwargs}
    assert json.loads(provider.clone_issue(**arguments))["error_code"] == code
    assert json.loads(fn(**arguments))["error_code"] == code
    assert not requests


def test_unconfigured_provider_returns_configuration_error(monkeypatch):
    for name in ("JIRA_URL", "JIRA_USERNAME", "JIRA_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    provider = JiraProvider(JiraConfig())
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["error_code"] == "configuration_error"
    assert result["cloned"] is False


def test_tool_schema_and_binding(native_jira):
    provider, requests = native_jira()
    server = MCPServer("test")
    register_tool(server, provider=provider)
    tool = asyncio.run(server.list_tools())[0]
    assert tool.name == "clone_issue"
    assert tool.input_schema["required"] == ["source_issue_key"]
    assert "provider" not in tool.input_schema["properties"]
    assert tool.input_schema["properties"]["clone_attachments"]["default"] is False
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    result = json.loads(
        server._tool_manager.get_tool("clone_issue").fn(" app-1 ", summary="新标题")
    )
    assert result["cloned"] is True
    assert result["summary"] == "新标题"
    assert len(requests) == 3


def test_tool_reports_missing_or_failed_provider():
    server = MCPServer("test")
    register_tool(server, default_provider="missing-clone-provider")
    fn = server._tool_manager.get_tool("clone_issue").fn
    assert json.loads(fn("APP-1"))["error_code"] == "provider_unavailable"
    failing = MagicMock()
    failing.clone_issue.side_effect = RuntimeError("private details")
    server = MCPServer("other")
    register_tool(server, provider=failing)
    result = json.loads(server._tool_manager.get_tool("clone_issue").fn("APP-1"))
    assert result["error_code"] == "provider_error"
    assert "private details" not in result["error"]


def test_parser_rejects_ambiguous_duplicate_forms():
    with pytest.raises(ValueError):
        ClonePage(FORM + FORM)


@pytest.mark.parametrize("code", ["dedupe_wait_timeout", "dedupe_result_unavailable"])
def test_duplicate_wait_failure_does_not_claim_nothing_was_created(
    native_jira, monkeypatch, code
):
    provider, requests = native_jira()
    monkeypatch.setattr(
        provider, "_begin_create_dedupe", lambda *args: ("wait", threading.Event())
    )
    monkeypatch.setattr(
        provider,
        "_wait_for_create_dedupe",
        lambda *args: json.dumps(
            {"error_code": code, "error": "Search Jira before retrying"}
        ),
    )
    result = json.loads(provider.clone_issue("APP-1"))
    assert result["created"] is None
    assert result["cloned"] is None
    assert result["outcome_unknown"] is True
    assert result["retry_blocked"] is True
    assert not requests
