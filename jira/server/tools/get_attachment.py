"""The public ``get_attachment`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import GET_ATTACHMENT_DESCRIPTION
from server.tools._common import ensure_json_result, error_response, provider_or_error


def register(mcp: MCPServer, default_provider: str = "jira") -> None:
    @mcp.tool(
        name="get_attachment",
        title="Download Jira Attachment",
        description=GET_ATTACHMENT_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_attachment(
        attachment_id: Annotated[str, Field(pattern=r"^\d+$", description="Numeric Jira attachment ID.")],
        save_to: Annotated[str, Field(min_length=1, max_length=4096, description="Authorized local output path.")],
    ) -> str:
        normalized_id = attachment_id.strip() if isinstance(attachment_id, str) else ""
        if not normalized_id.isdigit():
            return error_response("invalid_attachment_id", f"Invalid attachment ID: {attachment_id!r}", id=normalized_id)
        provider, error = provider_or_error(default_provider)
        if error:
            return error_response("provider_unavailable", "Jira provider is unavailable", id=normalized_id)
        try:
            raw = provider.get_attachment(normalized_id, save_to=save_to)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                id=normalized_id,
            )
        return ensure_json_result(raw, id=normalized_id)
