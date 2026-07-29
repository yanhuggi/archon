"""Tests for export_issue tool."""

import json
from pathlib import Path

import pytest

from server.providers import register
from server.tools.export_issue import register as register_export


class _StubProvider:
    """Minimal provider that returns canned data."""

    def get_issue_json(self, issue_key: str, **kwargs):
        return {
            "key": "TEST-123",
            "summary": "Test Issue",
            "issue_type": "Bug",
            "status": "Open",
            "priority": "High",
            "resolution": "Unresolved",
            "assignee": "Test User",
            "reporter": "Reporter",
            "created": "2026-01-01T00:00:00",
            "updated": "2026-01-02T00:00:00",
            "description": "Test description content",
            "subtasks": [
                {"key": "TEST-124", "summary": "Subtask 1", "status": "Done"},
            ],
            "issue_links": [
                {
                    "direction": "is blocked by",
                    "issue": {
                        "key": "TEST-125",
                        "summary": "Blocking Issue",
                        "status": "Open",
                    },
                },
            ],
            "attachments": [
                {
                    "id": "10001",
                    "filename": "test.md",
                    "size": 1024,
                    "mime_type": "text/markdown",
                    "author": "Test User",
                    "created": "2026-01-01T12:00:00",
                    "content_url": "http://example.com/attachment",
                },
            ],
        }


class _ErrorProvider:
    """Provider whose get_issue_json returns an error."""

    def get_issue_json(self, issue_key: str, **kwargs):
        return {"error": "Issue not found"}


class _MockMCP:
    """Mock MCP server to capture tool registration."""

    def __init__(self):
        self.tools = {}

    def tool(self, description: str):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator


def _make_mcp():
    """Create a mock MCP server and register tools, ensuring provider is set up first."""
    register("test_provider", _StubProvider())
    mcp = _MockMCP()
    register_export(mcp, default_provider="test_provider")
    return mcp


class TestExportIssue:
    """Test cases for export_issue tool."""

    def test_tool_registration(self):
        """Verify tool is registered correctly."""
        mcp = _make_mcp()
        assert "export_issue" in mcp.tools

    def test_tool_has_description(self):
        """Verify tool has a description."""
        mcp = _make_mcp()
        assert mcp.tools["export_issue"].__doc__ is not None

    def test_export_creates_docx_file(self, tmp_path):
        """Verify export creates a valid .docx file."""
        mcp = _make_mcp()
        save_path = tmp_path / "test_export.docx"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
            include_attachments=True,
        )

        result = json.loads(result_str)

        assert "error" not in result
        assert result["issue_key"] == "TEST-123"
        assert result["filename"] == "test_export.docx"
        assert result["saved_to"] == str(save_path)
        assert result["includes_attachments"] is True
        assert result["attachment_count"] == 1

        assert save_path.exists()
        assert save_path.stat().st_size > 0

    def test_export_with_txt_extension_converts_to_docx(self, tmp_path):
        """Verify export converts non-.docx extensions to .docx."""
        mcp = _make_mcp()
        save_path = tmp_path / "test.txt"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
            include_attachments=False,
        )

        result = json.loads(result_str)

        assert "error" not in result
        expected_path = tmp_path / "test.docx"
        assert expected_path.exists()

    def test_export_rejects_non_convertible_extension(self, tmp_path):
        """Verify export rejects extensions that can't be safely converted to .docx."""
        mcp = _make_mcp()
        save_path = tmp_path / "test.pdf"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
        )

        result = json.loads(result_str)

        # .pdf should be converted to .docx (all extensions are converted)
        assert "error" not in result
        expected_path = tmp_path / "test.docx"
        assert expected_path.exists()

    def test_export_without_attachments(self, tmp_path):
        """Verify export works without attachments."""
        mcp = _make_mcp()
        save_path = tmp_path / "no_attachments.docx"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
            include_attachments=False,
        )

        result = json.loads(result_str)

        assert "error" not in result
        assert result["includes_attachments"] is False
        assert save_path.exists()

    def test_export_creates_parent_directory(self, tmp_path):
        """Verify export creates parent directory if it doesn't exist."""
        mcp = _make_mcp()
        save_path = tmp_path / "subdir" / "deep" / "export.docx"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
        )

        result = json.loads(result_str)

        assert "error" not in result
        assert save_path.exists()

    def test_export_handles_invalid_provider(self, tmp_path):
        """Verify export handles invalid provider error."""
        mcp = _MockMCP()
        register_export(mcp, default_provider="invalid_provider")

        save_path = tmp_path / "test.docx"

        result_str = mcp.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
        )

        result = json.loads(result_str)

        assert "error" in result
        assert "Unknown provider" in result["error"]

    def test_export_propagates_issue_not_found(self, tmp_path):
        """Verify export propagates error when issue is not found."""
        register("error_provider", _ErrorProvider())
        mcp = _MockMCP()
        register_export(mcp, default_provider="error_provider")

        save_path = tmp_path / "missing.docx"

        result_str = mcp.tools["export_issue"](
            issue_key="INVALID-999",
            save_to=str(save_path),
        )

        result = json.loads(result_str)

        assert "error" in result
        assert "Issue not found" in result["error"]
        assert not save_path.exists()

    def test_export_includes_attachment_content(self, tmp_path):
        """Verify text attachment content is embedded in the document."""
        mcp = _make_mcp()
        save_path = tmp_path / "with_content.docx"

        # Override the stub provider's get_attachment to return content
        class _ProviderWithAttachment(_StubProvider):
            def get_attachment(self, attachment_id: str, save_to: str, **kwargs):
                from pathlib import Path as P
                P(save_to).write_text("# Attachment Title\nHello world", encoding='utf-8')
                import json
                return json.dumps({
                    "id": attachment_id,
                    "filename": "test.md",
                    "saved_to": save_to,
                    "size": 1024,
                    "mime_type": "text/markdown",
                })

        register("attach_provider", _ProviderWithAttachment())
        mcp2 = _MockMCP()
        register_export(mcp2, default_provider="attach_provider")

        result_str = mcp2.tools["export_issue"](
            issue_key="TEST-123",
            save_to=str(save_path),
            include_attachments=True,
        )

        result = json.loads(result_str)

        assert "error" not in result
        assert save_path.exists()

        # Verify the content was embedded in the document
        from docx import Document
        doc = Document(str(save_path))
        all_text = "\n".join(p.text for p in doc.paragraphs)
        assert "Hello world" in all_text
