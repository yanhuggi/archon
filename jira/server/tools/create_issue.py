"""The public ``create_issue`` MCP tool."""

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import CREATE_ISSUE_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_my_permissions import PROJECT_KEY_RE


def register(
    mcp: MCPServer,
    default_provider: str = "jira",
    provider: JiraProvider | None = None,
) -> None:
    @mcp.tool(
        name="create_issue",
        title="Create Jira Issue",
        description=CREATE_ISSUE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def create_issue(
        project_key: Annotated[
            str,
            Field(min_length=1, max_length=100, description="Exact Jira project key."),
        ],
        issue_type: Annotated[
            str,
            Field(min_length=1, max_length=255, description="Exact Jira issue type ID or name."),
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                min_length=1,
                max_length=50,
                description=(
                    "Jira REST API fields object excluding project and issuetype. "
                    "Use exact field IDs and Jira-native JSON values."
                ),
            ),
        ],
        idempotency_key: Annotated[
            str | None,
            Field(
                max_length=200,
                description=(
                    "Optional stable key for retries of one user submission. "
                    "The same key cannot be reused with different fields."
                ),
            ),
        ] = None,
    ) -> str:
        normalized_project = project_key.strip().upper() if isinstance(project_key, str) else ""
        normalized_type = issue_type.strip() if isinstance(issue_type, str) else ""
        if not PROJECT_KEY_RE.fullmatch(normalized_project):
            return error_response(
                "invalid_project_key",
                f"Invalid project key: {project_key!r}",
                project_key=normalized_project,
                created=False,
            )
        if not normalized_type or len(normalized_type) > 255:
            return error_response(
                "invalid_issue_type",
                "issue_type must contain 1 to 255 characters",
                project_key=normalized_project,
                created=False,
            )
        if not isinstance(fields, dict) or not fields:
            return error_response(
                "invalid_fields",
                "fields must be a non-empty object",
                project_key=normalized_project,
                created=False,
            )
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            return error_response(
                "invalid_idempotency_key",
                "idempotency_key must be a string",
                project_key=normalized_project,
                created=False,
            )
        normalized_idempotency_key = idempotency_key.strip() if idempotency_key else None
        if normalized_idempotency_key == "":
            normalized_idempotency_key = None
        if normalized_idempotency_key is not None and (
            len(normalized_idempotency_key) > 200
            or any(ord(char) < 32 for char in normalized_idempotency_key)
        ):
            return error_response(
                "invalid_idempotency_key",
                "idempotency_key must contain at most 200 printable characters",
                project_key=normalized_project,
                created=False,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                project_key=normalized_project,
                created=False,
            )
        try:
            raw = resolved_provider.create_issue(
                normalized_project,
                issue_type=normalized_type,
                fields=fields,
                idempotency_key=normalized_idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                project_key=normalized_project,
                created=False,
            )
        return ensure_json_result(raw, project_key=normalized_project, created=False)
