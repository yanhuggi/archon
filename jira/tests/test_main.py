"""Tests for the archon-jira MCP server entry point."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from mcp import Client
from mcp.types import TextContent

from server.config import JiraConfig
from server.main import (
    SERVER_NAME,
    _build_parser,
    _config_from_args,
    create_server,
    load_environment,
    main,
)
from server.providers.jira import JiraProvider


def test_load_environment_uses_first_file_without_override() -> None:
    with (
        patch("server.main.Path.exists", side_effect=[False, True]),
        patch("server.main.load_dotenv") as mock_load,
    ):
        loaded = load_environment()
    assert isinstance(loaded, Path)
    mock_load.assert_called_once_with(loaded, override=False)


def test_create_server_always_exposes_stable_tools() -> None:
    with patch("server.main.atexit.register"):
        server = create_server(JiraConfig())
    tools = asyncio.run(server.list_tools())
    prompts = asyncio.run(server.list_prompts())
    assert [tool.name for tool in tools] == [
        "get_current_user",
        "get_my_permissions",
        "search_jql_fields",
        "get_jql_value_suggestions",
        "search_issues",
        "get_issue",
        "get_issue_changelog",
        "get_transitions",
        "get_comments",
        "add_comment",
        "update_comment",
        "delete_comment",
        "create_issue",
        "clone_issue",
        "update_issue",
        "link_issue_blocks",
        "remove_issue_blocks",
        "remove_issue_clone",
        "transition_issue",
        "get_attachment",
        "export_issue",
    ]
    assert all("provider" not in tool.input_schema["properties"] for tool in tools)
    issue = next(tool for tool in tools if tool.name == "get_issue")
    assert issue.annotations.read_only_hint is True
    assert issue.annotations.destructive_hint is False
    attachment = next(tool for tool in tools if tool.name == "get_attachment")
    assert attachment.input_schema["required"] == ["attachment_id"]
    assert attachment.annotations.read_only_hint is True
    assert attachment.annotations.destructive_hint is False
    assert prompts == []
    assert server._lowlevel_server.name == SERVER_NAME


def test_get_issue_without_field_id_preserves_legacy_markdown_over_mcp() -> None:
    markdown = "# APP-123 - Example\n\n**关联任务：**\n- Blocks APP-124\n  "
    provider = MagicMock(spec=JiraProvider)
    provider.get_issue.return_value = markdown
    with patch("server.main.JiraProvider", return_value=provider), patch("server.main.atexit.register"):
        server = create_server(JiraConfig())

    async def call_tool():
        async with Client(server) as mcp:
            tools = await mcp.list_tools()
            issue = next(tool for tool in tools.tools if tool.name == "get_issue")
            assert issue.input_schema["required"] == ["issue_key"]
            assert "field_id" in issue.input_schema["properties"]
            return await mcp.call_tool("get_issue", {"issue_key": " app-123 "})

    result = asyncio.run(call_tool())

    provider.get_issue.assert_called_once_with("APP-123")
    assert result.is_error is False
    assert result.content == [TextContent(type="text", text=markdown)]


@pytest.mark.parametrize("oversize", [False, True], ids=["full-text", "over-limit"])
def test_get_issue_text_field_over_mcp(oversize: bool) -> None:
    field_id = "customfield_12345"
    value = "字" * 60_001 if oversize else "  \t开头\r\n| `\\\"" + "正文\n" * 4000 + "末尾 🧪\n  "
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/jira/rest/api/2/issue/APP-123"
        assert request.url.params.multi_items() == [("fields", field_id)]
        assert request.headers["Cookie"] == "JSESSIONID=fake-session"
        return httpx.Response(200, json={"fields": {field_id: value, "summary": "not requested"}})

    config = JiraConfig()
    provider = JiraProvider(config)
    with httpx.Client(
        base_url="https://jira.example.invalid/jira/",
        transport=httpx.MockTransport(handle_request),
        cookies={"JSESSIONID": "fake-session"},
    ) as client:
        provider._client = client
        with patch("server.main.JiraProvider", return_value=provider), patch("server.main.atexit.register"):
            server = create_server(config)

        async def call_tool():
            async with Client(server) as mcp:
                return await mcp.call_tool("get_issue", {"issue_key": " app-123 ", "field_id": field_id})

        result = asyncio.run(call_tool())

    assert result.is_error is False
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    payload = json.loads(result.content[0].text)
    if oversize:
        assert payload["issue_key"] == "APP-123"
        assert payload["field_id"] == field_id
        assert payload["error_code"] == "field_too_large"
        assert payload["error"]
        assert payload["length"] == 60_001
        assert payload["max_length"] == 60_000
        assert "value" not in payload
    else:
        assert payload == {"issue_key": "APP-123", "field_id": field_id, "value": value}
    assert len(requests) == 1


def test_servers_keep_their_provider_binding() -> None:
    class FakeProvider:
        def __init__(self, label: str) -> None:
            self.label = label

        def search_issues(self, jql: str, **kwargs) -> str:
            return json.dumps({"provider": self.label, "results": [], "result_count": 0})

        def close(self) -> None:
            pass

    first = FakeProvider("first")
    second = FakeProvider("second")
    with patch("server.main.JiraProvider", side_effect=[first, second]), patch("server.main.atexit.register"):
        server_one = create_server(JiraConfig())
        server_two = create_server(JiraConfig())

    result_one = server_one._tool_manager.get_tool("search_issues").fn("project = TEST")
    result_two = server_two._tool_manager.get_tool("search_issues").fn("project = TEST")
    assert json.loads(result_one)["provider"] == "first"
    assert json.loads(result_two)["provider"] == "second"


def test_main_runs_stdio_by_default() -> None:
    fake_server = MagicMock()
    with (
        patch("server.main.JiraConfig.from_env", return_value=JiraConfig()),
        patch("server.main.create_server", return_value=fake_server),
    ):
        main([])
    fake_server.run.assert_called_once_with(transport="stdio")


def test_main_runs_streamable_http_with_overrides() -> None:
    fake_server = MagicMock()
    with (
        patch("server.main.JiraConfig.from_env", return_value=JiraConfig()),
        patch("server.main.create_server", return_value=fake_server),
    ):
        main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    fake_server.run.assert_called_once_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=9000,
        streamable_http_path="/mcp",
        stateless_http=False,
    )


def test_cli_rejects_invalid_port() -> None:
    args = _build_parser().parse_args(["--port", "0"])
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _config_from_args(args, JiraConfig())
