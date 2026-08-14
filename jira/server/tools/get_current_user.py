"""The public ``get_current_user`` MCP tool."""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from server.instructions import GET_CURRENT_USER_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error


def register(
    mcp: MCPServer,
    default_provider: str = "jira",
    provider: JiraProvider | None = None,
) -> None:
    @mcp.tool(
        name="get_current_user",
        title="Get Current Jira User",
        description=GET_CURRENT_USER_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_current_user() -> str:
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                authenticated=False,
            )
        try:
            raw = resolved_provider.get_current_user()
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                authenticated=False,
            )
        return ensure_json_result(raw, authenticated=False)
