"""get_issue tool definition."""

import json

from mcp.server.fastmcp import FastMCP

from server.providers import get_provider


def register(mcp: FastMCP, default_provider: str = "jira") -> None:
    """Register the get_issue tool on the given MCP server.

    Args:
        mcp: The FastMCP instance.
        default_provider: Default Jira provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Get full details of a Jira issue by its key (e.g. 'PROJ-123'). Use this tool "
            "when: the user wants to see all information about a specific issue; they need "
            "issue links (blocks, is-blocked-by, clones, etc.) with relationship types; "
            "they want sub-tasks or parent task information; they need the full description, "
            "acceptance criteria, or custom fields. Returns comprehensive issue data "
            "including links with direction/type, sub-tasks, parent, and attachment metadata."
        )
    )
    def get_issue(
        issue_key: str,
        provider: str = default_provider,
    ) -> str:
        """Get full details of a Jira issue.

        Args:
            issue_key: Jira issue key (e.g. 'PROJ-123').
            provider: Jira provider backend to use.

        Returns:
            JSON string with full issue details including links, subtasks, and attachments.
        """
        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        return p.get_issue(issue_key)
