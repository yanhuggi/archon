"""The public ``transition_issue`` MCP tool."""

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import TRANSITION_ISSUE_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="transition_issue",
        title="Transition Jira Issue",
        description=TRANSITION_ISSUE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def transition_issue(
        issue_key: Annotated[str, Field(min_length=3, max_length=100)],
        transition_id: Annotated[str, Field(pattern=r"^\d+$")],
        fields: Annotated[
            dict[str, Any] | None,
            Field(
                max_length=50,
                description="Optional Jira transition-screen fields, using native REST API JSON values.",
            ),
        ] = None,
    ) -> str:
        normalized_key = issue_key.strip().upper() if isinstance(issue_key, str) else ""
        normalized_id = transition_id.strip() if isinstance(transition_id, str) else ""
        if not ISSUE_KEY_RE.fullmatch(normalized_key):
            return error_response(
                "invalid_issue_key",
                f"Invalid issue key: {issue_key!r}",
                issue_key=normalized_key,
                transition_id=normalized_id,
                transitioned=False,
            )
        if not normalized_id.isdigit():
            return error_response(
                "invalid_transition_id",
                f"Invalid transition ID: {transition_id!r}",
                issue_key=normalized_key,
                transition_id=normalized_id,
                transitioned=False,
            )
        if fields is not None and not isinstance(fields, dict):
            return error_response(
                "invalid_fields",
                "fields must be an object when provided",
                issue_key=normalized_key,
                transition_id=normalized_id,
                transitioned=False,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                issue_key=normalized_key,
                transition_id=normalized_id,
                transitioned=False,
            )
        try:
            raw = resolved_provider.transition_issue(
                normalized_key,
                normalized_id,
                fields=fields,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                issue_key=normalized_key,
                transition_id=normalized_id,
                transitioned=False,
            )
        return ensure_json_result(
            raw,
            issue_key=normalized_key,
            transition_id=normalized_id,
            transitioned=False,
        )
