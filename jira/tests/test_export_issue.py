"""Tests for the export_issue MCP tool."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from docx import Document

from server.config import JiraConfig
from server.providers import register
from server.tools.export_issue import register as register_tool


class StubProvider:
    def get_issue_json(self, issue_key: str, **kwargs) -> dict:
        return {
            "key": issue_key,
            "summary": "Test Issue",
            "issue_type": "Bug",
            "status": "Open",
            "priority": "High",
            "resolution": "Unresolved",
            "assignee": "Test User",
            "reporter": "Reporter",
            "created": "2026-01-01",
            "updated": "2026-01-02",
            "description": "Test description",
            "subtasks": [{"key": "TEST-2", "summary": "Subtask", "status": "Done"}],
            "issue_links": [{
                "direction": "is blocked by",
                "issue": {"key": "TEST-3", "summary": "Blocker", "status": "Open"},
            }],
            "attachments": [{
                "id": "10001",
                "filename": "notes.md",
                "size": 12,
                "mime_type": "text/markdown",
                "author": "Test User",
                "created": "2026-01-01",
            }],
        }

    def download_attachment(self, attachment_id: str, save_to: str, **kwargs) -> str:
        Path(save_to).write_text("Attachment content", encoding="utf-8")
        return json.dumps({"id": attachment_id, "saved_to": save_to})


def get_func(provider: str = "stub", config: JiraConfig | None = None):
    inner = MagicMock()
    mcp = MagicMock(spec=["tool"])
    mcp.tool = lambda **kwargs: inner
    register_tool(mcp, default_provider=provider, config=config)
    return inner.call_args[0][0]


def test_export_creates_docx_with_issue_and_attachment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    register("stub", StubProvider())
    destination = tmp_path / "issue.docx"
    result = json.loads(get_func()("test-1", str(destination), include_attachments=True))
    assert result["issue_key"] == "TEST-1"
    assert destination.exists()
    document = Document(destination)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Test description" in text
    assert "Attachment content" in text
    assert "is blocked by TEST-3" in text


def test_export_converts_suffix_to_docx(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    register("stub", StubProvider())
    result = json.loads(get_func()("TEST-1", str(tmp_path / "issue.txt"), include_attachments=False))
    assert result["filename"] == "issue.docx"
    assert (tmp_path / "issue.docx").exists()


def test_export_rejects_path_outside_allowed_directory(tmp_path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(allowed))
    data = json.loads(get_func()("TEST-1", str(tmp_path / "outside.docx")))
    assert data["error_code"] == "invalid_output_path"


def test_export_uses_server_config_instead_of_environment(tmp_path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    environment_directory = tmp_path / "environment"
    allowed.mkdir()
    environment_directory.mkdir()
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(environment_directory))
    register("stub", StubProvider())

    destination = allowed / "issue.docx"
    data = json.loads(
        get_func(config=JiraConfig(output_dir=allowed))(
            "TEST-1",
            str(destination),
            include_attachments=False,
        )
    )

    assert "error" not in data
    assert destination.exists()


def test_export_refuses_overwrite_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    existing = tmp_path / "issue.docx"
    existing.write_bytes(b"existing")
    data = json.loads(get_func()("TEST-1", str(existing)))
    assert data["error_code"] == "invalid_output_path"
    assert existing.read_bytes() == b"existing"


def test_export_reports_provider_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    provider = StubProvider()
    provider.get_issue_json = MagicMock(return_value={"error": "not found", "error_code": "not_found"})
    register("error", provider)
    data = json.loads(get_func("error")("TEST-1", str(tmp_path / "issue.docx")))
    assert data["error_code"] == "not_found"


def test_export_uses_controlled_temp_attachment_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_ALLOWED_OUTPUT_DIR", str(tmp_path))
    provider = StubProvider()
    issue = provider.get_issue_json("TEST-1")
    issue["attachments"][0]["filename"] = "../../outside.txt"
    provider.get_issue_json = MagicMock(return_value=issue)
    provider.download_attachment = MagicMock(side_effect=provider.download_attachment)
    register("unsafe-name", provider)
    result = json.loads(get_func("unsafe-name")("TEST-1", str(tmp_path / "issue.docx")))
    assert "error" not in result
    requested_path = Path(provider.download_attachment.call_args.args[1])
    assert requested_path.name.startswith("attachment-")
    assert requested_path.is_relative_to(tmp_path)
    assert not (tmp_path.parent / "outside.txt").exists()
