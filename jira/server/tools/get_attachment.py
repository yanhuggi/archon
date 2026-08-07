"""The public read-only ``get_attachment`` MCP tool."""

import base64
import binascii
import json
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from server.instructions import GET_ATTACHMENT_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error

_TEXT_MIME_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/sql",
    "application/yaml",
    "application/x-yaml",
}


def _is_text_mime(mime_type: str) -> bool:
    return mime_type.startswith("text/") or mime_type in _TEXT_MIME_TYPES


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="get_attachment",
        title="Read Jira Attachment",
        description=GET_ATTACHMENT_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_attachment(
        attachment_id: Annotated[str, Field(pattern=r"^\d+$", description="Numeric Jira attachment ID.")],
    ) -> Any:
        normalized_id = attachment_id.strip() if isinstance(attachment_id, str) else ""
        if not normalized_id.isdigit():
            return error_response("invalid_attachment_id", f"Invalid attachment ID: {attachment_id!r}", id=normalized_id)
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response("provider_unavailable", "Jira provider is unavailable", id=normalized_id)
        try:
            raw = resolved_provider.get_attachment(normalized_id)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                id=normalized_id,
            )
        normalized = ensure_json_result(raw, id=normalized_id)
        try:
            payload = json.loads(normalized)
        except (TypeError, ValueError):
            return normalized
        if not isinstance(payload, dict) or "error" in payload:
            return normalized

        encoded = payload.pop("content_base64", "")
        mime_type = str(payload.get("mime_type") or "application/octet-stream")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            return error_response(
                "invalid_provider_response",
                "Jira attachment content was not valid base64",
                id=normalized_id,
            )

        if mime_type.startswith("image/"):
            metadata = json.dumps(payload, ensure_ascii=False)
            return [
                TextContent(type="text", text=metadata),
                ImageContent(data=base64.b64encode(content).decode("ascii"), mimeType=mime_type),
            ]
        if _is_text_mime(mime_type):
            payload["content"] = content.decode("utf-8", errors="replace")
            return json.dumps(payload, ensure_ascii=False)

        payload["readable"] = False
        payload["reason"] = "Only text and image attachments are returned inline"
        return json.dumps(payload, ensure_ascii=False)
