"""export_issue tool definition."""

import json
import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Pt
from mcp.server import MCPServer

from server.providers import get_provider


def register(mcp: MCPServer, default_provider: str = "jira") -> None:
    """Register the export_issue tool on the given MCP server.

    Args:
        mcp: The MCPServer instance.
        default_provider: Default Jira provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Export a Jira issue to a .docx file with all details and attachment content. "
            "Use this tool when: the user wants to save issue information as a Word document; "
            "they need to include all issue details, descriptions, links, and attachment content "
            "in a formatted document; they need an offline copy of the issue for review or sharing. "
            "The export includes issue metadata, description, subtasks, issue links, and attachment "
            "content (text files are embedded, binary files are referenced)."
        )
    )
    def export_issue(
        issue_key: str,
        save_to: str,
        include_attachments: bool = True,
        provider: str = default_provider,
    ) -> str:
        """Export a Jira issue to a .docx file.

        Args:
            issue_key: Jira issue key (e.g. 'PROJ-123').
            save_to: Local file path to save the .docx file to.
            include_attachments: Whether to include attachment content in the document.
            provider: Jira provider backend to use.

        Returns:
            JSON string with saved_to path, filename, size, and summary of exported content.
        """
        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        save_path = Path(save_to).with_suffix(".docx")

        # Get issue details
        issue_json = p.get_issue_json(issue_key)
        if "error" in issue_json:
            return json.dumps(issue_json, ensure_ascii=False)

        try:
            doc = Document()

            # Title
            doc.add_heading(f"{issue_key} - {issue_json.get('summary', '')}", 0)

            # Metadata section
            doc.add_heading("Issue Details", level=1)
            table = doc.add_table(rows=0, cols=2)
            table.style = 'Table Grid'

            metadata = [
                ("Type", issue_json.get("issue_type", "")),
                ("Status", issue_json.get("status", "")),
                ("Priority", issue_json.get("priority", "")),
                ("Assignee", issue_json.get("assignee", "Unassigned")),
                ("Reporter", issue_json.get("reporter", "")),
                ("Created", issue_json.get("created", "")),
                ("Updated", issue_json.get("updated", "")),
                ("Resolution", issue_json.get("resolution", "Unresolved")),
            ]

            for label, value in metadata:
                row = table.add_row()
                row.cells[0].text = label
                row.cells[1].text = value

            # Description section
            description = issue_json.get("description", "")
            if description:
                doc.add_heading("Description", level=1)
                doc.add_paragraph(description)

            # Subtasks section
            subtasks = issue_json.get("subtasks", [])
            if subtasks:
                doc.add_heading("Subtasks", level=1)
                for st in subtasks:
                    doc.add_paragraph(
                        f"{st.get('key', '')} - {st.get('summary', '')} ({st.get('status', '')})",
                        style='List Bullet'
                    )

            # Issue links section
            issue_links = issue_json.get("issue_links", [])
            if issue_links:
                doc.add_heading("Related Issues", level=1)
                for link in issue_links:
                    direction = link.get("direction", "")
                    linked_issue = link.get("issue", {})
                    rel_type = link.get("relationship", "")
                    doc.add_paragraph(
                        f"{rel_type} {linked_issue.get('key', '')} - {linked_issue.get('summary', '')} ({linked_issue.get('status', '')})",
                        style='List Bullet'
                    )

            # Attachments section
            attachments = issue_json.get("attachments", [])
            if attachments and include_attachments:
                doc.add_heading("Attachments", level=1)

                for att in attachments:
                    att_id = att.get("id", "")
                    filename = att.get("filename", "")

                    doc.add_heading(filename, level=2)
                    doc.add_paragraph(f"ID: {att_id}")
                    doc.add_paragraph(f"Size: {att.get('size', 0) / 1024 / 1024:.2f} MB")
                    doc.add_paragraph(f"Author: {att.get('author', '')}")
                    doc.add_paragraph(f"Created: {att.get('created', '')}")
                    doc.add_paragraph(f"MIME Type: {att.get('mime_type', '')}")

                    # Try to download and include content for text-based attachments
                    if att.get("mime_type", "").startswith("text/") or filename.endswith(('.md', '.txt', '.json', '.csv', '.xml', '.yaml', '.yml')):
                        temp_file = Path(tempfile.gettempdir()) / f"temp_{att_id}_{filename}"
                        try:
                            result = p.get_attachment(att_id, str(temp_file))
                            result_data = json.loads(result)

                            if "error" not in result_data and temp_file.exists():
                                content = temp_file.read_text(encoding='utf-8', errors='ignore')
                                doc.add_paragraph("Content:")
                                doc.add_paragraph(content)
                        except Exception as e:
                            doc.add_paragraph(f"(Could not load attachment content: {e})")
                        finally:
                            if temp_file.exists():
                                temp_file.unlink(missing_ok=True)

            # Save document
            save_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(save_path.resolve()))

            return json.dumps(
                {
                    "issue_key": issue_key,
                    "saved_to": str(save_path.resolve()),
                    "filename": save_path.name,
                    "size": save_path.stat().st_size,
                    "includes_attachments": include_attachments,
                    "attachment_count": len(attachments),
                },
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"error": f"Export failed: {e}"}, ensure_ascii=False)
