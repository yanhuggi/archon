"""search_issues tool definition."""

import json

from mcp.server.fastmcp import FastMCP

from server.providers import get_provider


def register(mcp: FastMCP, default_provider: str = "jira") -> None:
    """Register the search_issues tool on the given MCP server.

    Args:
        mcp: The FastMCP instance.
        default_provider: Default Jira provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Search Jira issues using JQL (Jira Query Language). Use this tool when: "
            "the user asks to find, list, or search for Jira tasks/issues; they mention "
            "a project, assignee, status, sprint, or any JQL filter condition; they want "
            "to see open issues, bugs, stories, or epics. Examples of JQL: "
            "'project = MYPROJ AND status = Open', 'assignee = currentUser()', "
            "'issuetype = Bug AND priority = High'. Returns issue key, summary, "
            "status, assignee, issue type, priority, and labels."
        )
    )
    def search_issues(
        jql: str,
        max_results: int = 50,
        start_at: int = 0,
        provider: str = default_provider,
    ) -> str:
        """Search Jira issues using JQL.

        Args:
            jql: JQL query string (e.g. "project = MYPROJ AND status = Open").
            max_results: Maximum number of results to return (1-200, default 50).
            start_at: Offset for pagination (default 0).
            provider: Jira provider backend to use.

        Returns:
            JSON string with jql, total count, results list, and result_count.
        """
        max_results = min(max(max_results, 1), 200)
        start_at = max(start_at, 0)

        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        return p.search_issues(jql, max_results=max_results, start_at=start_at)
