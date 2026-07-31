"""The public ``search_jql_fields`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import SEARCH_JQL_FIELDS_DESCRIPTION
from server.tools._common import ensure_json_result, error_response, provider_or_error


def register(mcp: MCPServer, default_provider: str = "jira") -> None:
    @mcp.tool(
        name="search_jql_fields",
        title="Discover Jira JQL Fields",
        description=SEARCH_JQL_FIELDS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def search_jql_fields(
        query: Annotated[str, Field(max_length=200)] = "",
        max_results: Annotated[int, Field(ge=1, le=200)] = 50,
        start_at: Annotated[int, Field(ge=0)] = 0,
        refresh: bool = False,
    ) -> str:
        normalized_query = query.strip() if isinstance(query, str) else ""
        provider, error = provider_or_error(default_provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                query=normalized_query,
                fields=[],
                result_count=0,
            )
        try:
            raw = provider.search_jql_fields(
                normalized_query,
                max_results=min(max(int(max_results), 1), 200),
                start_at=max(int(start_at), 0),
                refresh=bool(refresh),
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                query=normalized_query,
                fields=[],
                result_count=0,
            )
        return ensure_json_result(raw, query=normalized_query, fields=[], result_count=0)
