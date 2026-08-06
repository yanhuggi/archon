"""The public ``search_issues`` MCP tool."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import SEARCH_ISSUES_DESCRIPTION
from server.providers import JiraProvider
from server.tools._common import ensure_json_result, error_response, provider_or_error

MAX_JQL_LENGTH = 4000


def register(mcp: MCPServer, default_provider: str = "jira", provider: JiraProvider | None = None) -> None:
    @mcp.tool(
        name="search_issues",
        title="Search Jira Issues",
        description=SEARCH_ISSUES_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def search_issues(
        jql: Annotated[str, Field(min_length=1, max_length=MAX_JQL_LENGTH, description="Jira Query Language expression.")],
        max_results: Annotated[int, Field(ge=1, le=200)] = 50,
        start_at: Annotated[int, Field(ge=0)] = 0,
    ) -> str:
        if not isinstance(jql, str) or not jql.strip():
            return error_response("invalid_jql", "jql must not be empty", jql="", results=[], result_count=0)
        normalized_jql = jql.strip()
        if len(normalized_jql) > MAX_JQL_LENGTH:
            return error_response(
                "invalid_jql",
                f"jql must be at most {MAX_JQL_LENGTH} characters",
                jql=normalized_jql,
                results=[],
                result_count=0,
            )
        resolved_provider, error = provider_or_error(default_provider, provider)
        if error:
            return error_response(
                "provider_unavailable",
                "Jira provider is unavailable",
                jql=normalized_jql,
                results=[],
                result_count=0,
            )
        try:
            raw = resolved_provider.search_issues(
                normalized_jql,
                max_results=min(max(int(max_results), 1), 200),
                start_at=max(int(start_at), 0),
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return error_response(
                "provider_error",
                f"Jira provider failed: {type(exc).__name__}: {exc}",
                jql=normalized_jql,
                results=[],
                result_count=0,
            )
        return ensure_json_result(raw, jql=normalized_jql, results=[], result_count=0)
