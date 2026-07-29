"""get_attachment tool definition."""

import json

from mcp.server import MCPServer

from server.providers import get_provider


def register(mcp: MCPServer, default_provider: str = "jira") -> None:
    """Register the get_attachment tool on the given MCP server.

    Args:
        mcp: The MCPServer instance.
        default_provider: Default Jira provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Download a Jira attachment to a local file. Use this tool when: "
            "the user wants to save a file attached to a Jira issue to disk; "
            "they need to view an image, read a log file, or examine a document. "
            "First call get_issue to find attachment IDs and filenames, then use "
            "this tool with the attachment_id and a save_to path. The parent "
            "directory will be created if it doesn't exist."
        )
    )
    def get_attachment(
        attachment_id: str,
        save_to: str,
        provider: str = default_provider,
    ) -> str:
        """Download a Jira attachment to a local file.

        Args:
            attachment_id: Jira attachment ID (numeric string, from get_issue results).
            save_to: Local file path to save the attachment to.
            provider: Jira provider backend to use.

        Returns:
            JSON string with saved_to path, filename, size, and mime_type.
        """
        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        return p.get_attachment(attachment_id, save_to=save_to)
