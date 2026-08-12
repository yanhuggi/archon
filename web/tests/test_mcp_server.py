from __future__ import annotations

import asyncio
import json

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
