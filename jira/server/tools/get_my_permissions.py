"""The public ``get_my_permissions`` MCP tool."""

import re
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import GET_MY_PERMISSIONS_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE

PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def register(
    mcp: MCPServer,
    default_provider: str = "jira",
    provider: JiraProvider | None = None,
) -> None:
    @mcp.tool(
        name="get_my_permissions",
        title="Get My Jira Permissions",
        description=GET_MY_PERMISSIONS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_my_permissions(
        issue_key: Annotated[
            str | None,
            Field(max_length=100, description="Optional Jira issue key used as the permission context."),
        ] = None,
        project_key: Annotated[
            str | None,
            Field(max_length=100, description="Optional Jira project key used as the permission context."),
        ] = None,
        permissions: Annotated[
            list[str] | None,
            Field(
                max_length=50,
                description="Optional exact Jira permission keys, IDs, or names to return.",
            ),
        ] = None,
    ) -> str:
        normalized_issue = issue_key.strip().upper() if isinstance(issue_key, str) else None
        normalized_project = project_key.strip().upper() if isinstance(project_key, str) else None
        if normalized_issue == "":
            normalized_issue = None
        if normalized_project == "":
            normalized_project = None
        if normalized_issue and normalized_project:
            return error_response(
                "invalid_permission_context",
                "Provide either issue_key or project_key, not both",
                permissions=[],
                permission_count=0,
            )
        if normalized_issue and not ISSUE_KEY_RE.fullmatch(normalized_issue):
            return error_response(
                "invalid_issue_key",
                f"Invalid issue key: {issue_key!r}",
                issue_key=normalized_issue,
                permissions=[],
                permission_count=0,
            )
        if normalized_project and not PROJECT_KEY_RE.fullmatch(normalized_project):
            return error_response(
                "invalid_project_key",
                f"Invalid project key: {project_key!r}",
                project_key=normalized_project,
                permissions=[],
                permission_count=0,
            )

        normalized_permissions: list[str] | None = None
        if permissions is not None:
            if not isinstance(permissions, list) or len(permissions) > 50:
                return error_response(
                    "invalid_permissions",
                    "permissions must be an array containing at most 50 values",
                    permissions=[],
                    permission_count=0,
                )
            normalized_permissions = []
            seen: set[str] = set()
            for value in permissions:
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
                    return error_response(
                        "invalid_permissions",
                        "permission values must contain 1 to 100 characters",
                        permissions=[],
                        permission_count=0,
                    )
                candidate = value.strip()
                identity = candidate.casefold()
                if identity not in seen:
                    normalized_permissions.append(candidate)
                    seen.add(identity)

        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                issue_key=normalized_issue,
                project_key=normalized_project,
                permissions=[],
                permission_count=0,
            )
        try:
            raw = resolved_provider.get_my_permissions(
                issue_key=normalized_issue,
                project_key=normalized_project,
                permissions=normalized_permissions,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                issue_key=normalized_issue,
                project_key=normalized_project,
                permissions=[],
                permission_count=0,
            )
        return ensure_json_result(raw, permissions=[], permission_count=0)
