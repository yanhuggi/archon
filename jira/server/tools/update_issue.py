"""The public ``update_issue`` MCP tool."""

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import UPDATE_ISSUE_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="update_issue",
        title="Update Jira Issue",
        description=UPDATE_ISSUE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def update_issue(
        issue_key: Annotated[
            str,
            Field(min_length=3, max_length=100, description="Jira issue key, for example PROJ-123."),
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                min_length=1,
                max_length=50,
                description=(
                    "Jira REST API fields object. Use field IDs as keys and Jira-native JSON values; "
                    "null clears a field when Jira permits it."
                ),
            ),
        ],
    ) -> str:
        normalized_key = issue_key.strip().upper() if isinstance(issue_key, str) else ""
        if not ISSUE_KEY_RE.fullmatch(normalized_key):
            return error_response(
                "invalid_issue_key",
                f"Invalid issue key: {issue_key!r}",
                issue_key=normalized_key,
            )
        if not isinstance(fields, dict) or not fields:
            return error_response(
                "invalid_fields",
                "fields must be a non-empty object",
                issue_key=normalized_key,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                issue_key=normalized_key,
            )
        try:
            raw = resolved_provider.update_issue(normalized_key, fields=fields)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                issue_key=normalized_key,
            )
        return ensure_json_result(raw, issue_key=normalized_key, updated=False, updated_fields=[])
