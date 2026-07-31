"""The public ``web_search`` MCP tool."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import WEB_SEARCH_DESCRIPTION
from server.providers import get_provider


LOGGER = logging.getLogger(__name__)
MAX_QUERY_LENGTH = 500
MIN_RESULTS = 1
MAX_RESULTS = 20
DEFAULT_RESULTS = 8
TimeRange = Literal["day", "week", "month", "year"]


def _json_error(query: str, code: str, message: str) -> str:
    """Return the stable error envelope used by the MCP tool."""

    return json.dumps(
        {
            "query": query,
            "results": [],
            "result_count": 0,
            "error": message,
            "error_code": code,
        },
        ensure_ascii=False,
    )


def _normalize_query(query: object) -> tuple[str, str | None]:
    """Normalize user input and return ``(query, error_message)``."""

    if not isinstance(query, str):
        return "", "query must be a string"
    normalized = " ".join(query.split())
    if not normalized:
        return "", "query must not be empty"
    if len(normalized) > MAX_QUERY_LENGTH:
        return "", f"query must be at most {MAX_QUERY_LENGTH} characters"
    return normalized, None


def _normalize_max_results(value: object) -> int | None:
    """Convert a direct Python call into a safe result limit."""

    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(result, MIN_RESULTS), MAX_RESULTS)


def _ensure_json_response(raw: object, query: str) -> str:
    """Keep provider output JSON-compatible and preserve the public contract."""

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return _json_error(query, "invalid_provider_response", "Search provider returned invalid JSON")
    elif isinstance(raw, dict):
        data = raw
    else:
        return _json_error(query, "invalid_provider_response", "Search provider returned an unsupported response")

    if not isinstance(data, dict):
        return _json_error(query, "invalid_provider_response", "Search provider response must be a JSON object")

    # Providers should return this envelope themselves, but filling missing
    # fields here makes the MCP contract robust when a provider is replaced.
    data.setdefault("query", query)
    results = data.get("results")
    if not isinstance(results, list):
        data["results"] = []
        results = data["results"]
    data.setdefault("result_count", len(results))
    return json.dumps(data, ensure_ascii=False)


def register(mcp: MCPServer) -> None:
    """Register the ``web_search`` tool on an MCP server."""

    @mcp.tool(
        name="web_search",
        title="Web Search",
        description=WEB_SEARCH_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        # Keep the established JSON-in-text response compatible with existing
        # MCP clients. The envelope is still machine-readable and versioned.
        structured_output=False,
    )
    def web_search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_QUERY_LENGTH,
                description="Focused web search terms (1-500 characters).",
            ),
        ],
        max_results: Annotated[
            int,
            Field(ge=MIN_RESULTS, le=MAX_RESULTS, description="Number of results (1-20)."),
        ] = DEFAULT_RESULTS,
        time_range: TimeRange | None = None,
    ) -> str:
        """Search the public web and return a stable JSON result envelope."""

        normalized_query, query_error = _normalize_query(query)
        if query_error:
            return _json_error(str(query) if isinstance(query, str) else "", "invalid_query", query_error)

        normalized_limit = _normalize_max_results(max_results)
        if normalized_limit is None:
            return _json_error(normalized_query, "invalid_max_results", "max_results must be an integer")

        try:
            provider = get_provider("duckduckgo")
        except ValueError as exc:
            return _json_error(normalized_query, "provider_unavailable", str(exc))

        kwargs: dict[str, object] = {}
        if time_range is not None:
            kwargs["time_range"] = time_range

        try:
            raw = provider.search(normalized_query, max_results=normalized_limit, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive for third-party providers
            LOGGER.exception("web_search provider failed")
            return _json_error(
                normalized_query,
                "provider_error",
                f"Search provider failed: {type(exc).__name__}: {exc}",
            )
        return _ensure_json_response(raw, normalized_query)
