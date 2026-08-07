"""The public ``update_comment`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import UPDATE_COMMENT_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE

MAX_COMMENT_LENGTH = 32767


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="update_comment",
        title="Update Jira Comment",
        description=UPDATE_COMMENT_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def update_comment(
        issue_key: Annotated[str, Field(min_length=3, max_length=100)],
        comment_id: Annotated[str, Field(pattern=r"^\d+$")],
        body: Annotated[str, Field(min_length=1, max_length=MAX_COMMENT_LENGTH)],
    ) -> str:
        normalized_key = issue_key.strip().upper() if isinstance(issue_key, str) else ""
        normalized_id = comment_id.strip() if isinstance(comment_id, str) else ""
        if not ISSUE_KEY_RE.fullmatch(normalized_key):
            return error_response(
                "invalid_issue_key",
                f"Invalid issue key: {issue_key!r}",
                issue_key=normalized_key,
                comment_id=normalized_id,
                updated=False,
            )
        if not normalized_id.isdigit():
            return error_response(
                "invalid_comment_id",
                f"Invalid comment ID: {comment_id!r}",
                issue_key=normalized_key,
                comment_id=normalized_id,
                updated=False,
            )
        if not isinstance(body, str) or not body.strip():
            return error_response(
                "invalid_comment_body",
                "Comment body must not be empty",
                issue_key=normalized_key,
                comment_id=normalized_id,
                updated=False,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                issue_key=normalized_key,
                comment_id=normalized_id,
                updated=False,
            )
        try:
            raw = resolved_provider.update_comment(normalized_key, normalized_id, body=body)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                issue_key=normalized_key,
                comment_id=normalized_id,
                updated=False,
            )
        return ensure_json_result(raw, issue_key=normalized_key, comment_id=normalized_id, updated=False)
