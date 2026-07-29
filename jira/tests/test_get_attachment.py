"""Tests for server.tools.get_attachment — get_attachment MCP tool."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.jira import JiraProvider
from server.tools.get_attachment import register as register_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock httpx.Client with is_closed=False."""
    client = MagicMock()
    client.is_closed = False
    return client


class _StubProvider:
    def search_issues(self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_issue(self, issue_key: str, **kwargs) -> str:
        return "{}"

    def get_comments(self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs) -> str:
        return "{}"

    def get_attachment(self, attachment_id: str, save_to: str = "", **kwargs) -> str:
        return json.dumps({
            "id": attachment_id,
            "filename": "stub.txt",
            "saved_to": os.path.abspath(save_to),
            "size": 12,
            "mime_type": "text/plain",
        })


@pytest.fixture(autouse=True)
def _register_stub() -> None:
    register("stub", _StubProvider())


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_register_creates_tool() -> None:
    mcp = MagicMock()
    register_tool(mcp, default_provider="stub")
    mcp.tool.assert_called_once()
    inner_decorator = mcp.tool.return_value
    inner_decorator.assert_called_once()
    func = inner_decorator.call_args[0][0]
    assert func.__name__ == "get_attachment"


class _MockMCP:
    def __init__(self) -> None:
        self.captured_tools: dict[str, dict] = {}

    def tool(self, **kwargs: object) -> callable:
        tool_meta = kwargs

        def decorator(func: callable) -> callable:
            self.captured_tools[func.__name__] = {
                "function": func,
                "description": tool_meta.get("description", ""),
            }
            return func

        return decorator


def test_tool_description_mentions_save_to() -> None:
    mcp = _MockMCP()
    register_tool(mcp)
    desc = mcp.captured_tools["get_attachment"]["description"].lower()
    assert "save_to" in desc or "save" in desc
    assert "attachment" in desc


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------


def _get_func(default_provider: str = "stub") -> callable:
    mcp = MagicMock(spec=["tool"])
    inner_decorator = MagicMock()
    mcp.tool = lambda **kw: inner_decorator
    register_tool(mcp, default_provider=default_provider)
    return inner_decorator.call_args[0][0]


def test_get_attachment_calls_provider() -> None:
    func = _get_func()
    with patch("server.tools.get_attachment.get_provider", wraps=None) as mock_get:
        mock_get.return_value = _StubProvider()
        result = func("10001", save_to="/tmp/test.txt")
        data = json.loads(result)

    mock_get.assert_called_once_with("stub")
    assert data["id"] == "10001"
    assert "saved_to" in data


def test_get_attachment_unknown_provider() -> None:
    func = _get_func()
    result = func("10001", save_to="/tmp/test.txt", provider="nonexistent")
    assert "Unknown provider" in result


# ---------------------------------------------------------------------------
# Integration with real provider
# ---------------------------------------------------------------------------


def test_real_provider_text_integration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")
    register("jira", JiraProvider())

    mock_client = _make_mock_client()

    # First call: metadata
    meta_resp = MagicMock()
    meta_resp.status_code = 200
    meta_resp.json.return_value = {
        "id": "10001",
        "filename": "log.txt",
        "size": 19,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/secure/attachment/10001/log.txt",
    }
    meta_resp.raise_for_status = MagicMock()

    # Second call: download stream
    content_resp = MagicMock()
    content_resp.status_code = 200
    content_resp.raise_for_status = MagicMock()
    content_resp.__enter__ = MagicMock(return_value=content_resp)
    content_resp.__exit__ = MagicMock(return_value=False)
    content_resp.iter_bytes.return_value = [b"error log content"]

    mock_client.stream.return_value = content_resp
    # For metadata call
    mock_client.get.return_value = meta_resp

    save_path = str(tmp_path / "log.txt")

    func = _get_func("jira")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = func("10001", save_to=save_path, provider="jira")
    data = json.loads(result)

    assert data["filename"] == "log.txt"
    assert data["saved_to"] == save_path
    assert os.path.exists(save_path)
    with open(save_path, "rb") as f:
        assert f.read() == b"error log content"


def test_real_provider_binary_integration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")
    register("jira", JiraProvider())

    mock_client = _make_mock_client()

    meta_resp = MagicMock()
    meta_resp.status_code = 200
    meta_resp.json.return_value = {
        "id": "10002",
        "filename": "image.png",
        "size": 8,
        "mimeType": "image/png",
        "content": "https://jira.example.com/secure/attachment/10002/image.png",
    }
    meta_resp.raise_for_status = MagicMock()

    content_resp = MagicMock()
    content_resp.status_code = 200
    content_resp.raise_for_status = MagicMock()
    content_resp.__enter__ = MagicMock(return_value=content_resp)
    content_resp.__exit__ = MagicMock(return_value=False)
    content_resp.iter_bytes.return_value = [b"\x89PNG"]

    mock_client.get.return_value = meta_resp
    mock_client.stream.return_value = content_resp

    save_path = str(tmp_path / "image.png")

    func = _get_func("jira")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = func("10002", save_to=save_path, provider="jira")
    data = json.loads(result)

    assert data["filename"] == "image.png"
    assert os.path.exists(save_path)
    with open(save_path, "rb") as f:
        assert f.read() == b"\x89PNG"


def test_parent_dir_created(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """get_attachment creates parent directory if it doesn't exist."""
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_USERNAME", "user")
    monkeypatch.setenv("JIRA_PASSWORD", "pass")
    register("jira", JiraProvider())

    mock_client = _make_mock_client()
    meta_resp = MagicMock()
    meta_resp.status_code = 200
    meta_resp.json.return_value = {
        "id": "10003",
        "filename": "test.txt",
        "size": 4,
        "mimeType": "text/plain",
        "content": "https://jira.example.com/attachment/10003",
    }
    meta_resp.raise_for_status = MagicMock()

    content_resp = MagicMock()
    content_resp.raise_for_status = MagicMock()
    content_resp.__enter__ = MagicMock(return_value=content_resp)
    content_resp.__exit__ = MagicMock(return_value=False)
    content_resp.iter_bytes.return_value = [b"data"]

    mock_client.get.return_value = meta_resp
    mock_client.stream.return_value = content_resp

    nested_path = str(tmp_path / "subdir" / "deep" / "test.txt")

    func = _get_func("jira")
    with patch.object(JiraProvider, "_get_client", return_value=mock_client):
        result = func("10003", save_to=nested_path, provider="jira")
    data = json.loads(result)

    assert os.path.exists(nested_path)
    assert data["saved_to"] == nested_path
