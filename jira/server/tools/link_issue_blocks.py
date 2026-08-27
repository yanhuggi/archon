"""The public ``link_issue_blocks`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import LINK_ISSUE_BLOCKS_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error
from server.tools.get_issue import ISSUE_KEY_RE


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="link_issue_blocks",
        title="Link Jira Issues: Blocks",
        description=LINK_ISSUE_BLOCKS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def link_issue_blocks(
        source_issue_key: Annotated[
            str,
            Field(
                min_length=3,
                max_length=100,
                description="Jira key for the blocking issue, for example PROJ-123.",
            ),
        ],
        target_issue_key: Annotated[
            str,
            Field(
                min_length=3,
                max_length=100,
                description="Jira key for the issue that is blocked, for example PROJ-456.",
            ),
        ],
    ) -> str:
        source = source_issue_key.strip().upper() if isinstance(source_issue_key, str) else ""
        target = target_issue_key.strip().upper() if isinstance(target_issue_key, str) else ""
        if not ISSUE_KEY_RE.fullmatch(source):
            return error_response(
                "invalid_issue_key",
                f"Invalid source issue key: {source_issue_key!r}",
                source_issue_key=source,
                target_issue_key=target,
                linked=False,
            )
        if not ISSUE_KEY_RE.fullmatch(target):
            return error_response(
                "invalid_issue_key",
                f"Invalid target issue key: {target_issue_key!r}",
                source_issue_key=source,
                target_issue_key=target,
                linked=False,
            )
        if source == target:
            return error_response(
                "invalid_issue_link",
                "The source and target issue keys must differ",
                source_issue_key=source,
                target_issue_key=target,
                linked=False,
            )

        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                source_issue_key=source,
                target_issue_key=target,
                linked=False,
            )
        try:
            raw = resolved_provider.link_issue_blocks(source, target)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                source_issue_key=source,
                target_issue_key=target,
                linked=False,
            )
        return ensure_json_result(
            raw,
            source_issue_key=source,
            target_issue_key=target,
            linked=False,
        )
