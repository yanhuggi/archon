"""Tests for the get_attachment MCP tool."""

import inspect
import base64
import json
from unittest.mock import MagicMock

import pytest
from mcp.types import ImageContent
from server.providers import register
from server.tools.get_attachment import MAX_INLINE_TEXT_BYTES, _trim_to_character_boundary
from server.tools.get_attachment import register as register_tool


class StubProvider:
    def get_attachment(self, attachment_id: str, **kwargs) -> str:
        return json.dumps({
            "id": attachment_id,
            "filename": "example.txt",
            "size": 4,
            "mime_type": "text/plain",
            "content_base64": base64.b64encode(b"test").decode("ascii"),
        })


class ImageStubProvider:
    def get_attachment(self, attachment_id: str, **kwargs) -> str:
        return json.dumps({
            "id": attachment_id,
            "filename": "example.png",
            "size": 4,
            "mime_type": "image/png",
            "content_base64": base64.b64encode(b"png!").decode("ascii"),
        })


def get_func(provider: str = "stub"):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider)
    return inner.call_args[0][0]


def test_attachment_calls_provider() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()("10001"))
    assert data["id"] == "10001"
    assert data["content"] == "test"


def test_attachment_rejects_invalid_id() -> None:
    data = json.loads(get_func()("abc"))
    assert data["error_code"] == "invalid_attachment_id"


def test_attachment_reports_missing_provider() -> None:
    data = json.loads(get_func("missing")("10001"))
    assert data["error_code"] == "provider_unavailable"


def test_attachment_returns_inline_image_content() -> None:
    register("image", ImageStubProvider())
    result = get_func("image")("10001")
    assert isinstance(result, list)
    assert isinstance(result[1], ImageContent)
    assert result[1].mime_type == "image/png"


def test_attachment_does_not_expose_provider_argument() -> None:
    signature = inspect.signature(get_func())
    assert "provider" not in signature.parameters
    assert "save_to" not in signature.parameters


def test_attachment_bounds_inline_text() -> None:
    """A large text attachment cannot flood one tool result."""

    class LargeTextProvider:
        def get_attachment(self, attachment_id: str, **kwargs) -> str:
            body = b"x" * (MAX_INLINE_TEXT_BYTES + 500)
            return json.dumps({
                "id": attachment_id,
                "filename": "big.log",
                "size": len(body),
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(body).decode("ascii"),
            })

    register("large", LargeTextProvider())
    data = json.loads(get_func("large")("10001"))

    assert data["truncated"] is True
    assert len(data["content"]) == MAX_INLINE_TEXT_BYTES


def test_attachment_budget_is_measured_in_bytes_not_characters() -> None:
    """CJK text costs ~4x the tokens per character, so the cap counts UTF-8 bytes."""

    class ChineseTextProvider:
        def get_attachment(self, attachment_id: str, **kwargs) -> str:
            body = "测试内容".encode() * (MAX_INLINE_TEXT_BYTES // 12 + 100)
            return json.dumps({
                "id": attachment_id,
                "filename": "big.txt",
                "size": len(body),
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(body).decode("ascii"),
            })

    register("chinese", ChineseTextProvider())
    data = json.loads(get_func("chinese")("10001"))

    assert data["truncated"] is True
    # A character-based cap would have returned ~4x this many bytes.
    assert len(data["content"].encode()) <= MAX_INLINE_TEXT_BYTES
    assert "测试" in data["content"]
    assert "�" not in data["content"]  # no character split at the boundary


@pytest.mark.parametrize(
    "text", ["abcdef", "中文中文", "ééééé", "😀😀😀"], ids=["ascii", "cjk", "latin1", "emoji"]
)
def test_byte_trim_never_splits_a_character(text: str) -> None:
    """Every cut point yields strictly decodable UTF-8, for 1- to 4-byte characters."""
    encoded = text.encode()
    for limit in range(1, len(encoded) + 1):
        trimmed = _trim_to_character_boundary(encoded, limit)
        assert len(trimmed) <= limit
        trimmed.decode("utf-8")  # strict: raises if a sequence was cut


def test_attachment_marks_small_text_as_complete() -> None:
    register("stub", StubProvider())
    data = json.loads(get_func()("10001"))
    assert data["truncated"] is False
    assert data["content"] == "test"
