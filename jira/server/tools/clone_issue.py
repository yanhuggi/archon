"""The public ``clone_issue`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import CLONE_ISSUE_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE


def register(
    mcp: MCPServer,
    default_provider: str = "jira",
    provider: JiraProvider | None = None,
) -> None:
    @mcp.tool(
        name="clone_issue",
        title="Clone Jira Issue",
        description=CLONE_ISSUE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def clone_issue(
        source_issue_key: Annotated[
            str,
            Field(
                min_length=3,
                max_length=100,
                description="Jira key of the issue to clone, for example PROJ-123.",
            ),
        ],
        summary: Annotated[
            str | None,
            Field(
                max_length=255,
                description="Optional new summary; omit to keep Jira's native clone summary.",
            ),
        ] = None,
        clone_attachments: Annotated[
            bool,
            Field(description="Copy attachments using Jira's native clone option."),
        ] = False,
        clone_subtasks: Annotated[
            bool,
            Field(description="Copy subtasks using Jira's native clone option."),
        ] = False,
        clone_links: Annotated[
            bool,
            Field(description="Copy issue links using Jira's native clone option."),
        ] = False,
        idempotency_key: Annotated[
            str | None,
            Field(
                max_length=200,
                description="Optional stable key for retries of one clone submission; reuse with identical options.",
            ),
        ] = None,
    ) -> str:
        source = (
            source_issue_key.strip().upper()
            if isinstance(source_issue_key, str)
            else ""
        )
        context = {"source_issue_key": source, "created": False, "cloned": False}
        if not ISSUE_KEY_RE.fullmatch(source):
            return error_response(
                "invalid_issue_key",
                f"Invalid source issue key: {source_issue_key!r}",
                **context,
            )
        if summary is not None:
            if (
                not isinstance(summary, str)
                or not summary.strip()
                or len(summary.strip()) > 255
                or any(ord(char) < 32 for char in summary)
            ):
                return error_response(
                    "invalid_summary",
                    "summary must contain 1 to 255 printable characters",
                    **context,
                )
            summary = summary.strip()
        if any(
            type(value) is not bool
            for value in (clone_attachments, clone_subtasks, clone_links)
        ):
            return error_response(
                "invalid_clone_options", "Clone options must be booleans", **context
            )
        if idempotency_key is not None:
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or len(idempotency_key.strip()) > 200
                or any(ord(char) < 32 for char in idempotency_key)
            ):
                return error_response(
                    "invalid_idempotency_key",
                    "idempotency_key must contain 1 to 200 printable characters",
                    **context,
                )
            idempotency_key = idempotency_key.strip()
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable", "Jira provider is unavailable", **context
            )
        try:
            raw = resolved_provider.clone_issue(
                source,
                summary=summary,
                clone_attachments=clone_attachments,
                clone_subtasks=clone_subtasks,
                clone_links=clone_links,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}",
                **context,
            )
        return ensure_json_result(raw, **context)
