"""The public ``get_transitions`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import GET_TRANSITIONS_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="get_transitions",
        title="Get Jira Issue Transitions",
        description=GET_TRANSITIONS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_transitions(
        issue_key: Annotated[str, Field(min_length=3, max_length=100)],
    ) -> str:
        normalized_key = issue_key.strip().upper() if isinstance(issue_key, str) else ""
        if not ISSUE_KEY_RE.fullmatch(normalized_key):
            return error_response(
                "invalid_issue_key",
                f"Invalid issue key: {issue_key!r}",
                issue_key=normalized_key,
                transitions=[],
                transition_count=0,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                issue_key=normalized_key,
                transitions=[],
                transition_count=0,
            )
        try:
            raw = resolved_provider.get_transitions(normalized_key)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                issue_key=normalized_key,
                transitions=[],
                transition_count=0,
            )
        return ensure_json_result(raw, issue_key=normalized_key, transitions=[], transition_count=0)
