"""Tests for the get_attachment MCP tool."""

import inspect
import json
from unittest.mock import MagicMock

from server.providers import register
from server.tools.get_attachment import register as register_tool


class StubProvider:
    def get_attachment(self, attachment_id: str, save_to: str, **kwargs) -> str:
        return json.dumps({
            "id": attachment_id,
            "filename": "example.txt",
            "saved_to": save_to,
            "size": 4,
            "mime_type": "text/plain",
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_attachment_calls_provider() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()("10001", "/tmp/example.txt"))
    assert data["id"] == "10001"
    assert data["saved_to"] == "/tmp/example.txt"


def test_attachment_rejects_invalid_id() -> None:
    data = json.loads(get_func()("abc", "/tmp/example.txt"))
    assert data["error_code"] == "invalid_attachment_id"


def test_attachment_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("10001", "/tmp/example.txt"))
    assert data["error_code"] == "provider_unavailable"


def test_attachment_does_not_expose_provider_argument() -> None:
    assert "provider" not in inspect.signature(get_func()).parameters
