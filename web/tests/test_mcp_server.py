from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from server.config import WebConfig
from server.main import create_server


def test_server_exposes_search_and_returns_missing_key_envelope() -> None:
    server = create_server(WebConfig())
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == ["web_search"]

    response = asyncio.run(server.call_tool("web_search", {"query": "current news"}))
    payload = response[0] if isinstance(response, tuple) else response
    data = json.loads(payload.content[0].text)
    assert data["error_code"] == "missing_api_key"
    assert data["results"] == []


def _tool_result_text(server, arguments: dict) -> str:
    response = asyncio.run(server.call_tool("web_search", arguments))
    payload = response[0] if isinstance(response, tuple) else response
    return payload.content[0].text


def test_tool_schema_advertises_required_argument_types_and_bounds() -> None:
    schema = asyncio.run(create_server(WebConfig()).list_tools())[0].input_schema
    properties = schema["properties"]
    assert schema["required"] == ["query"]
    assert properties["query"]["type"] == "string"
    assert properties["query"]["minLength"] == 1
    assert properties["query"]["maxLength"] == 500
    assert properties["max_results"]["type"] == "integer"
    assert properties["max_results"]["minimum"] == 1
    assert properties["max_results"]["maximum"] == 20


def test_invalid_tool_arguments_return_json_envelopes_without_upstream_calls() -> None:
    server = create_server(WebConfig())
    cases = [
        ({}, "invalid_query"),
        ({"query": ""}, "invalid_query"),
        ({"query": "x" * 501}, "invalid_query"),
        ({"query": 1}, "invalid_query"),
        ({"query": "query", "max_results": True}, "invalid_max_results"),
        ({"query": "query", "max_results": "3"}, "invalid_max_results"),
        ({"query": "query", "time_range": "decade"}, "invalid_time_range"),
    ]
    for arguments, expected_code in cases:
        data = json.loads(_tool_result_text(server, arguments))
        assert data["error_code"] == expected_code
        assert data["results"] == []


def test_tool_clamps_limits_before_calling_provider() -> None:
    server = create_server(WebConfig(tavily_api_key="tvly-test"))
    with patch("server.providers.tavily.urlopen") as opener:
        opener.return_value.__enter__.return_value.read.return_value = b'{"results": []}'
        _tool_result_text(server, {"query": "query", "max_results": 999})
    request = opener.call_args.args[0]
    assert json.loads(request.data)["max_results"] == 20
