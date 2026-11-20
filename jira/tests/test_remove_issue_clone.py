"""Tests for the clone relationship removal MCP tool."""

import asyncio
import json

import httpx
import pytest
from unittest.mock import MagicMock

from mcp.server import MCPServer

from server.providers import register
from server.providers.jira import JiraProvider
from server.tools.remove_issue_clone import register as register_tool


class StubProvider:
    def remove_issue_clone(self, source_issue_key: str, target_issue_key: str, **kwargs) -> str:
        return json.dumps({
            "source_issue_key": source_issue_key,
            "target_issue_key": target_issue_key,
            "link_type": "Cloners",
            "link_id": "10001",
            "removed": True,
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_remove_issue_clone_normalizes_keys_and_preserves_direction() -> None:
    register("stub", StubProvider())

    data = json.loads(get_func()("ylzhxt-19183", "ylzhxt-16820"))

    assert data == {
        "source_issue_key": "YLZHXT-19183",
        "target_issue_key": "YLZHXT-16820",
        "link_type": "Cloners",
        "link_id": "10001",
        "removed": True,
    }


def test_remove_issue_clone_rejects_invalid_or_identical_keys() -> None:
    assert json.loads(get_func()("not an issue", "TEST-2"))["error_code"] == "invalid_issue_key"
    assert json.loads(get_func()("TEST-1", "test-1"))["error_code"] == "invalid_issue_link"


def test_remove_issue_clone_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("TEST-1", "TEST-2"))
    assert data["error_code"] == "provider_unavailable"
    assert data["removed"] is False


def test_remove_issue_clone_schema_marks_destructive_remote_write() -> None:
    server = MCPServer("test")
    register_tool(server, provider=StubProvider())

    tool = asyncio.run(server.list_tools())[0]

    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
    assert tool.annotations.idempotent_hint is True
    assert tool.input_schema["required"] == ["source_issue_key", "target_issue_key"]
    assert "provider" not in tool.input_schema["properties"]


@pytest.mark.parametrize("source,target", [("TEST-1", "bad/key"), ("", "TEST-2")])
def test_tool_rejects_invalid_keys(source, target) -> None:
    provider = MagicMock()
    server = MCPServer("test")
    register_tool(server, provider=provider)
    data = json.loads(server._tool_manager.get_tool("remove_issue_clone").fn(source, target))
    assert data["error_code"] == "invalid_issue_key"
    provider.remove_issue_clone.assert_not_called()


@pytest.mark.parametrize("raw,code", [("not json", "invalid_provider_response"), ([], "invalid_provider_response")])
def test_tool_rejects_bad_provider_response(raw, code) -> None:
    provider = MagicMock()
    provider.remove_issue_clone.return_value = raw
    server = MCPServer("test")
    register_tool(server, provider=provider)
    data = json.loads(server._tool_manager.get_tool("remove_issue_clone").fn("TEST-1", "TEST-2"))
    assert data["error_code"] == code
    assert data["removed"] is False


def test_tool_reports_provider_exception() -> None:
    provider = MagicMock()
    provider.remove_issue_clone.side_effect = ValueError("failed")
    server = MCPServer("test")
    register_tool(server, provider=provider)
    data = json.loads(server._tool_manager.get_tool("remove_issue_clone").fn("TEST-1", "TEST-2"))
    assert data["error_code"] == "provider_error"
    assert data["removed"] is False


def clone_link(link_id="10001", side="inward", target="PROJ-2"):
    return {
        "id": link_id,
        "type": {"name": "Cloners", "inward": "is cloned by", "outward": "clones"},
        f"{side}Issue": {"key": target},
    }


@pytest.fixture
def provider():
    instance = JiraProvider()
    instance._request = MagicMock()
    yield instance
    instance.close()


def set_links(provider, links):
    provider._request.return_value.json.return_value = {"fields": {"issuelinks": links}}


@pytest.mark.parametrize("side", ["inward", "outward"])
def test_provider_removes_unique_original_to_clone_link(provider, side) -> None:
    link = clone_link(side=side, target="proj-2")
    if side == "outward":
        link["type"].update(inward="clones", outward="is cloned by")
    reverse_link = clone_link("10002", side="outward" if side == "inward" else "inward")
    reverse_link["type"] = dict(link["type"])
    set_links(provider, [
        {"id": "99", "type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-2"}},
        link,
        reverse_link,
    ])
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data == {
        "source_issue_key": "PROJ-1", "target_issue_key": "PROJ-2",
        "link_type": "Cloners", "link_id": "10001", "removed": True,
    }
    assert provider._request.call_args_list == [
        (("GET", "rest/api/2/issue/PROJ-1"), {"params": {"fields": "issuelinks"}}),
        (("DELETE", "rest/api/2/issueLink/10001"), {}),
    ]


@pytest.mark.parametrize("links", [
    [], [clone_link(side="outward")], [clone_link(target="PROJ-3")],
    [dict(clone_link(), type={"name": "Blocks", "inward": "is cloned by"})],
    [dict(clone_link(), type={"name": "Cloners"})],
    [dict(clone_link(), type={"name": "Cloners", "inward": "is cloned by", "outward": "is cloned by"})],
    [None, "bad", {}, dict(clone_link(), inwardIssue=None)],
])
def test_provider_does_not_delete_unmatched_or_reversed_links(provider, links) -> None:
    set_links(provider, links)
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == "issue_link_not_found"
    assert data["removed"] is False
    assert provider._request.call_count == 1


def test_provider_refuses_ambiguous_links(provider) -> None:
    set_links(provider, [clone_link(), clone_link("10002")])
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == "ambiguous_issue_link"
    assert data["matching_link_ids"] == ["10001", "10002"]
    assert data["removed"] is False
    assert provider._request.call_count == 1


@pytest.mark.parametrize("link_id", [None, "", 10001, "../10001", "10001/path"])
def test_provider_refuses_invalid_link_id(provider, link_id) -> None:
    set_links(provider, [clone_link(link_id)])
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == "invalid_provider_response"
    assert data["removed"] is False
    assert provider._request.call_count == 1


@pytest.mark.parametrize("payload", [None, [], {}, {"fields": {}}, {"fields": {"issuelinks": None}}])
def test_provider_refuses_malformed_issue_response(provider, payload) -> None:
    provider._request.return_value.json.return_value = payload
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == "invalid_provider_response"
    assert data["removed"] is False
    assert provider._request.call_count == 1


@pytest.mark.parametrize("source,target", [("bad/key", "PROJ-2"), ("PROJ-1", "bad/key"), ("PROJ-1", "proj-1")])
def test_provider_validates_keys_without_request(provider, source, target) -> None:
    data = json.loads(provider.remove_issue_clone(source, target))
    assert data["error_code"] in {"invalid_issue_key", "invalid_issue_link"}
    assert data["removed"] is False
    provider._request.assert_not_called()


@pytest.mark.parametrize("phase", ["lookup", "delete"])
@pytest.mark.parametrize("status,code", [(403, "authentication_error"), (404, "not_found"), (500, "upstream_error")])
def test_provider_reports_http_errors_without_retry(provider, phase, status, code) -> None:
    request = httpx.Request("DELETE", "https://jira.example.com/rest/api/2/issueLink/10001")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("failed", request=request, response=response)
    lookup = MagicMock()
    lookup.json.return_value = {"fields": {"issuelinks": [clone_link()]}}
    provider._request.side_effect = [error] if phase == "lookup" else [lookup, error]
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == code
    assert data["removed"] is False
    assert provider._request.call_count == (1 if phase == "lookup" else 2)


def test_provider_does_not_retry_unknown_delete_outcome(provider) -> None:
    lookup = MagicMock()
    lookup.json.return_value = {"fields": {"issuelinks": [clone_link()]}}
    provider._request.side_effect = [lookup, httpx.ReadTimeout("unknown outcome")]
    data = json.loads(provider.remove_issue_clone("PROJ-1", "PROJ-2"))
    assert data["error_code"] == "upstream_error"
    assert data["removed"] is False
    assert provider._request.call_count == 2
