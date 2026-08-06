"""The public ``web_search`` MCP tool."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.instructions import WEB_SEARCH_DESCRIPTION
from server.providers import SearchProvider, get_provider


LOGGER = logging.getLogger(__name__)
MAX_QUERY_LENGTH = 500
MIN_RESULTS = 1
MAX_RESULTS = 20
DEFAULT_RESULTS = 8
TIME_RANGES = ("day", "week", "month", "year")


class _Missing(str):
    """Sentinel marking an argument the caller never sent.

    ``None`` cannot serve this purpose: a client may legitimately send
    ``query: null``, and that has to be reported as an invalid value rather than
    as a missing one.

    It subclasses ``str`` so Pydantic can serialize it while building the JSON
    schema. A plain object triggers a PydanticJsonSchemaWarning on stderr, which
    would corrupt nothing but does pollute a stdio server's logs.
    ``_restore_required_query`` removes it from the published schema.
    """

    __slots__ = ()


_MISSING = _Missing()


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

    if query is _MISSING:
        return "", "query is required"
    if not isinstance(query, str):
        return "", "query must be a string"
    normalized = " ".join(query.split())
    if not normalized:
        return "", "query must not be empty"
    if len(normalized) > MAX_QUERY_LENGTH:
        return "", f"query must be at most {MAX_QUERY_LENGTH} characters"
    return normalized, None


def _normalize_max_results(value: object) -> int | None:
    """Clamp a genuine integer limit, rejecting every other type.

    ``int(value)`` would accept "3", " 3 ", and 1.5, silently searching with a
    limit the caller never asked for. ``bool`` is an ``int`` subclass, so it has
    to be excluded explicitly.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(max(value, MIN_RESULTS), MAX_RESULTS)


def _normalize_time_range(value: object) -> tuple[str | None, str | None]:
    """Normalize ``time_range`` and return ``(value, error_message)``."""

    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "time_range must be a string"
    normalized = value.strip().lower()
    if not normalized:
        return None, None
    if normalized not in TIME_RANGES:
        allowed = ", ".join(TIME_RANGES)
        return None, f"time_range must be one of: {allowed}"
    return normalized, None


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


def _published_schema(mcp: MCPServer) -> dict[str, object] | None:
    """Return the tool's live input schema, or ``None`` if it cannot be reached.

    The MCP SDK exposes no public accessor for a registered tool's schema, and
    ``list_tools()`` hands back a copy, so this reads an internal attribute. It
    is isolated here so the one unsupported access has a single call site and a
    single failure path.
    """

    try:
        schema = mcp._tool_manager.get_tool("web_search").parameters
    except Exception:  # noqa: BLE001 - any SDK change must degrade, not crash
        return None
    return schema if isinstance(schema, dict) else None


def _restore_required_query(mcp: MCPServer) -> None:
    """Advertise ``query`` as required despite its sentinel default.

    The sentinel exists so an omitted ``query`` reaches the function body and
    returns the documented envelope instead of a generic ToolError. It is an
    implementation detail, so strip it from the published schema and restore the
    ``required`` marker a client needs to see.

    This depends on an SDK internal that the dependency range does not promise.
    The tool keeps working if that access fails, but clients would be told
    ``query`` is optional and shown the sentinel default, so the outcome is
    verified and a failure is reported loudly rather than swallowed.
    """

    schema = _published_schema(mcp)
    if schema is not None:
        properties = schema.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("query"), dict):
            properties["query"].pop("default", None)
        required = schema.get("required")
        if not isinstance(required, list):
            schema["required"] = ["query"]
        elif "query" not in required:
            required.insert(0, "query")

    if not _query_is_advertised_as_required(mcp):
        LOGGER.warning(
            "Could not mark web_search's query argument as required in the published "
            "schema; this MCP SDK version exposes tool metadata differently. Calls "
            "still return the documented JSON envelope, but clients may treat query "
            "as optional. Please report this together with the installed mcp version."
        )


def _query_is_advertised_as_required(mcp: MCPServer) -> bool:
    """Check the outcome instead of assuming the adjustment above worked."""

    schema = _published_schema(mcp)
    if schema is None:
        return False
    required = schema.get("required")
    if not isinstance(required, list) or "query" not in required:
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not isinstance(properties.get("query"), dict):
        return False
    return "default" not in properties["query"]


def register(
    mcp: MCPServer,
    default_provider: str = "duckduckgo",
    provider: SearchProvider | None = None,
) -> None:
    """Register the ``web_search`` tool on an MCP server.

    ``provider`` binds this tool to one backend instance. Prefer it over
    ``default_provider``: the name-based registry is process-global, so two
    servers created in one process would otherwise share whichever provider
    registered last, along with its timeout, proxy, and rate-limit settings.
    """

    def _resolve_provider() -> SearchProvider:
        if provider is not None:
            return provider
        return get_provider(default_provider)

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
    # Types and bounds are advertised through json_schema_extra but deliberately
    # annotated as Any: any validation the SDK performs before the body runs
    # raises a generic ToolError instead of the documented JSON envelope. A
    # declared ``int`` would also silently coerce ``true`` to 1 and search on.
    # The body owns every check so all rejections share one shape.
    #
    # ``query`` carries a sentinel default for the same reason: a parameter with
    # no default is rejected by the SDK before the body runs, so omitting it
    # produced a non-JSON ToolError. ``_restore_required_query`` puts the
    # ``required`` marker back into the published schema afterwards.
    def web_search(
        query: Annotated[
            Any,
            Field(
                description="Focused web search terms (1-500 characters).",
                json_schema_extra={
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_QUERY_LENGTH,
                },
            ),
        ] = _MISSING,
        max_results: Annotated[
            Any,
            Field(
                description="Number of results (1-20).",
                json_schema_extra={
                    "type": "integer",
                    "minimum": MIN_RESULTS,
                    "maximum": MAX_RESULTS,
                },
            ),
        ] = DEFAULT_RESULTS,
        time_range: Annotated[
            Any,
            Field(
                description="Optional recency filter: day, week, month, or year.",
                json_schema_extra={"type": ["string", "null"], "enum": [*TIME_RANGES, None]},
            ),
        ] = None,
    ) -> str:
        """Search the public web and return a stable JSON result envelope."""

        normalized_query, query_error = _normalize_query(query)
        if query_error:
            return _json_error(str(query) if isinstance(query, str) else "", "invalid_query", query_error)

        normalized_limit = _normalize_max_results(max_results)
        if normalized_limit is None:
            return _json_error(normalized_query, "invalid_max_results", "max_results must be an integer")

        normalized_range, range_error = _normalize_time_range(time_range)
        if range_error:
            return _json_error(normalized_query, "invalid_time_range", range_error)

        try:
            active_provider = _resolve_provider()
        except ValueError as exc:
            return _json_error(normalized_query, "provider_unavailable", str(exc))

        kwargs: dict[str, object] = {}
        if normalized_range is not None:
            kwargs["time_range"] = normalized_range

        try:
            raw = active_provider.search(normalized_query, max_results=normalized_limit, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive for third-party providers
            LOGGER.exception("web_search provider failed")
            return _json_error(
                normalized_query,
                "provider_error",
                f"Search provider failed: {type(exc).__name__}: {exc}",
            )
        return _ensure_json_response(raw, normalized_query)

    _restore_required_query(mcp)
