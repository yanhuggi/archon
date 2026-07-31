"""The public ``get_jql_value_suggestions`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION
from server.tools._common import ensure_json_result, error_response, provider_or_error


def register(mcp: MCPServer, default_provider: str = "jira") -> None:
    @mcp.tool(
        name="get_jql_value_suggestions",
        title="Get Jira JQL Value Suggestions",
        description=GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def get_jql_value_suggestions(
        field: Annotated[
            str,
            Field(min_length=1, max_length=200, description="Exact Jira field name, ID, or JQL clause."),
        ],
        query: Annotated[str, Field(max_length=500)] = "",
        max_results: Annotated[int, Field(ge=1, le=200)] = 50,
        refresh: bool = False,
    ) -> str:
        normalized_field = field.strip() if isinstance(field, str) else ""
        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_field:
            return error_response(
                "invalid_jql_field",
                "field must not be empty",
                field="",
                suggestions=[],
                result_count=0,
            )
        provider, error = provider_or_error(default_provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                field=normalized_field,
                suggestions=[],
                result_count=0,
            )
        try:
            raw = provider.get_jql_value_suggestions(
                normalized_field,
                query=normalized_query,
                max_results=min(max(int(max_results), 1), 200),
                refresh=bool(refresh),
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                field=normalized_field,
                suggestions=[],
                result_count=0,
            )
        return ensure_json_result(
            raw,
            field=normalized_field,
            suggestions=[],
            result_count=0,
        )
