"""archon-jira MCP server entry point."""

import atexit
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer

# Load .env — checks project dir, CWD, then global config
for p in (
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
    Path.home() / ".config/archon-jira/.env",
):
    if p.exists():
        load_dotenv(p, override=True)
        break

from server.providers import register as register_provider
from server.providers.jira import JiraProvider
from server.tools.search_issues import register as register_search_issues
from server.tools.get_issue import register as register_get_issue
from server.tools.get_attachment import register as register_get_attachment

# Register Jira provider
jira_provider = JiraProvider()
register_provider("jira", jira_provider)

# Ensure HTTP connections are cleaned up on exit
atexit.register(jira_provider.close)

# Create MCP server
mcp = MCPServer("archon-jira")

# Register tools only if Jira connection is configured
if os.environ.get("JIRA_URL") and os.environ.get("JIRA_USERNAME"):
    register_search_issues(mcp, default_provider="jira")
    register_get_issue(mcp, default_provider="jira")
    register_get_attachment(mcp, default_provider="jira")
else:
    print(
        "Warning: JIRA_URL and/or JIRA_USERNAME not set. "
        "Jira tools will not be registered. "
        "Set JIRA_URL, JIRA_USERNAME, and JIRA_PASSWORD in environment or .env.",
        file=sys.stderr,
    )


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
