"""Tests for the public ``web_search`` MCP tool."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from server.providers import register
from server.providers.duckduckgo import DuckDuckGoProvider
from server.tools.web_search import register as register_tool


class _StubProvider:
    """Minimal provider used by tool-level tests."""

    def search(self, query: str, max_results: int = 10, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": query,
                "results": [
                    {"title": "stub", "url": "https://stub.example", "snippet": "stub"}
                ],
                "result_count": 1,
            }
        )


@pytest.fixture(autouse=True)
def _register_stub() -> None:
    register("duckduckgo", _StubProvider())
    yield


class _MockMCP:
    """Capture the tool decorator and its metadata."""

    def __init__(self) -> None:
        self.captured_tools: dict[str, dict[str, object]] = {}

    def tool(self, **metadata: object) -> callable:
        def decorator(func: callable) -> callable:
            name = str(metadata.get("name") or func.__name__)
            self.captured_tools[name] = {"function": func, **metadata}
            return func

        return decorator


def _get_web_search_func() -> callable:
    mcp = _MockMCP()
    register_tool(mcp)
    return mcp.captured_tools["web_search"]["function"]


def test_register_creates_read_only_open_world_tool() -> None:
    mcp = _MockMCP()
    register_tool(mcp)

    metadata = mcp.captured_tools["web_search"]
    assert metadata["title"] == "Web Search"
    assert metadata["structured_output"] is False
    annotations = metadata["annotations"]
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.open_world_hint is True


def test_tool_description_defines_use_and_non_use_boundaries() -> None:
    mcp = _MockMCP()
    register_tool(mcp)
    description = str(mcp.captured_tools["web_search"]["description"])

    assert "3-8 important words" in description
    assert "time_range" in description
    assert "Do not use it" in description
    assert "retry once" in description


def test_web_search_calls_duckduckgo_provider() -> None:
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=_StubProvider()) as mock_get:
        data = json.loads(func("hello"))

    mock_get.assert_called_once_with("duckduckgo")
    assert data["query"] == "hello"
    assert data["result_count"] == 1


def test_web_search_normalizes_query_and_forwards_limit() -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("  MCP   server\nrelease ", max_results=3)

    mock_search.assert_called_once_with("MCP server release", max_results=3)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (100, 20)],
)
def test_web_search_clamps_direct_python_result_limit(requested: int, expected: int) -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("query", max_results=requested)

    mock_search.assert_called_once_with("query", max_results=expected)


def test_web_search_passes_time_range_only_when_requested() -> None:
    stub = _StubProvider()
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            func("MCP release", time_range="week")

    mock_search.assert_called_once_with("MCP release", max_results=8, time_range="week")


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_web_search_rejects_empty_query(query: str) -> None:
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider") as mock_get:
        data = json.loads(func(query))

    mock_get.assert_not_called()
    assert data["error_code"] == "invalid_query"
    assert data["results"] == []
    assert data["result_count"] == 0


def test_web_search_rejects_overlong_query() -> None:
    data = json.loads(_get_web_search_func()("x" * 501))
    assert data["error_code"] == "invalid_query"
    assert "500" in data["error"]


def test_web_search_rejects_non_integer_limit() -> None:
    data = json.loads(_get_web_search_func()("query", max_results="many"))
    assert data["error_code"] == "invalid_max_results"


def test_web_search_rejects_boolean_limit() -> None:
    """``True`` would otherwise pass ``int()`` and request one result."""
    data = json.loads(_get_web_search_func()("query", max_results=True))
    assert data["error_code"] == "invalid_max_results"


@pytest.mark.parametrize("value", ["decade", "DAYS", "d"])
def test_web_search_rejects_unknown_time_range_with_envelope(value: str) -> None:
    """An unsupported range must not reach the provider as a silent no-op."""
    stub = _StubProvider()
    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            data = json.loads(_get_web_search_func()("query", time_range=value))

    mock_search.assert_not_called()
    assert data["error_code"] == "invalid_time_range"
    assert "day, week, month, year" in data["error"]


@pytest.mark.parametrize("value", ["WEEK", " week ", "Week"])
def test_web_search_normalizes_time_range_case_and_padding(value: str) -> None:
    stub = _StubProvider()
    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            _get_web_search_func()("query", time_range=value)

    mock_search.assert_called_once_with("query", max_results=8, time_range="week")


def test_web_search_treats_blank_time_range_as_unset() -> None:
    """A client sending "" should get an unfiltered search, not a rejection."""
    stub = _StubProvider()
    with patch("server.tools.web_search.get_provider", return_value=stub):
        with patch.object(stub, "search", wraps=stub.search) as mock_search:
            _get_web_search_func()("query", time_range="  ")

    mock_search.assert_called_once_with("query", max_results=8)


def test_bounds_are_advertised_without_schema_level_enforcement() -> None:
    """Schema bounds guide clients; the body enforces them as JSON envelopes.

    Field(min_length/max_length) would make the SDK raise a generic ToolError
    for an out-of-range value, so a rejection would not share the documented
    envelope shape with every other failure.
    """
    from server.config import WebConfig
    from server.main import create_server

    # Read the schema the SDK actually publishes rather than one derived from the
    # function signature, which is not what a client sees.
    schema = asyncio.run(create_server(WebConfig()).list_tools())[0].input_schema
    query_schema = schema["properties"]["query"]
    assert query_schema["minLength"] == 1
    assert query_schema["maxLength"] == 500

    # The bound is advertised, yet an over-long query still returns an envelope.
    func = _get_web_search_func()
    assert json.loads(func("x" * 501))["error_code"] == "invalid_query"


@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    [
        # Omitting a required argument is rejected by the SDK before the body
        # runs unless the parameter has a default, which produced a non-JSON
        # ToolError for the one call every client is most likely to get wrong.
        ({}, "invalid_query"),
        ({"max_results": 3}, "invalid_query"),
        ({"query": ""}, "invalid_query"),
        ({"query": "   "}, "invalid_query"),
        ({"query": "x" * 501}, "invalid_query"),
        ({"query": 123}, "invalid_query"),
        ({"query": None}, "invalid_query"),
        ({"query": "ok", "max_results": "many"}, "invalid_max_results"),
        # A declared ``int`` would coerce this to 1 and search anyway.
        ({"query": "ok", "max_results": True}, "invalid_max_results"),
        # int() would accept all three, searching with a limit nobody asked for.
        ({"query": "ok", "max_results": "3"}, "invalid_max_results"),
        ({"query": "ok", "max_results": " 3 "}, "invalid_max_results"),
        ({"query": "ok", "max_results": 1.5}, "invalid_max_results"),
        ({"query": "ok", "time_range": "decade"}, "invalid_time_range"),
        ({"query": "ok", "time_range": 123}, "invalid_time_range"),
    ],
)
def test_invalid_arguments_return_envelopes_through_a_real_server(
    arguments: dict, expected_code: str
) -> None:
    """Calling the function directly skips the validation an MCP client triggers.

    Any bound or type enforced by the annotation raises a generic ToolError
    before the body runs, so a real ``call_tool`` is the only way to prove that
    every rejection shares the documented envelope shape.
    """
    from server.config import WebConfig
    from server.main import create_server

    server = create_server(WebConfig(interval=0.0))

    class _UnusedDDGS:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("invalid arguments must not reach the upstream")

    with patch("server.providers.duckduckgo.DDGS", _UnusedDDGS):
        result = asyncio.run(server.call_tool("web_search", arguments))

    payload = result[0] if isinstance(result, tuple) else result
    data = json.loads(payload.content[0].text)

    assert data["error_code"] == expected_code
    assert data["results"] == []
    assert data["result_count"] == 0


@pytest.mark.parametrize(("requested", "expected"), [(0, 1), (999, 20)])
def test_out_of_range_limits_clamp_through_a_real_server(requested: int, expected: int) -> None:
    """The documented behavior is clamping, not a transport-level rejection."""
    from server.config import WebConfig
    from server.main import create_server

    server = create_server(WebConfig(interval=0.0))
    seen: list[object] = []

    class _RecordingDDGS:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _RecordingDDGS:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def text(self, query: str, **kwargs: object) -> list[dict[str, str]]:
            seen.append(kwargs.get("max_results"))
            return []

    with patch("server.providers.duckduckgo.DDGS", _RecordingDDGS):
        asyncio.run(server.call_tool("web_search", {"query": "ok", "max_results": requested}))

    assert seen == [expected]


def test_schema_still_advertises_types_and_bounds_to_clients() -> None:
    """Annotating as Any must not degrade what clients see in the tool schema."""
    from server.config import WebConfig
    from server.main import create_server

    schema = asyncio.run(create_server(WebConfig()).list_tools())[0].input_schema
    properties = schema["properties"]

    assert properties["query"]["type"] == "string"
    assert properties["query"]["minLength"] == 1
    assert properties["query"]["maxLength"] == 500
    assert properties["max_results"]["type"] == "integer"
    assert properties["max_results"]["minimum"] == 1
    assert properties["max_results"]["maximum"] == 20
    assert properties["time_range"]["enum"] == ["day", "week", "month", "year", None]
    # The sentinel default that lets an omitted query reach the body is an
    # implementation detail; clients must still be told the argument is required.
    assert schema["required"] == ["query"]
    assert "default" not in properties["query"]


def test_building_the_schema_emits_no_pydantic_warning() -> None:
    """A stderr warning at startup would pollute a stdio server's logs."""
    import warnings

    from server.config import WebConfig
    from server.main import create_server

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        asyncio.run(create_server(WebConfig()).list_tools())


def test_a_client_supplied_empty_query_is_not_read_as_missing() -> None:
    """The sentinel is compared by identity, so its value cannot be forged.

    It subclasses ``str`` to stay JSON-schema serializable, which means a client
    could send the same value. That must be reported as an empty query rather
    than as an omitted one.
    """
    from server.tools.web_search import _MISSING, _normalize_query

    assert _normalize_query(_MISSING)[1] == "query is required"
    assert _normalize_query(str(_MISSING))[1] == "query must not be empty"
    assert _normalize_query("")[1] == "query must not be empty"


def test_register_binds_a_provider_instance_over_the_global_registry() -> None:
    """Two servers in one process must not share whichever provider was last.

    The name-based registry is process-global, so resolving by name would make
    the first server use the second server's timeout, proxy, and rate limits.
    """
    bound = _StubProvider()
    mcp = _MockMCP()
    register_tool(mcp, provider=bound)
    func = mcp.captured_tools["web_search"]["function"]

    with patch("server.tools.web_search.get_provider") as mock_get:
        with patch.object(bound, "search", wraps=bound.search) as mock_search:
            data = json.loads(func("query"))

    mock_get.assert_not_called()
    mock_search.assert_called_once()
    assert data["result_count"] == 1


def test_web_search_returns_consistent_provider_unavailable_error() -> None:
    func = _get_web_search_func()
    with patch("server.tools.web_search.get_provider", side_effect=ValueError("Unknown provider")):
        data = json.loads(func("query"))

    assert data["query"] == "query"
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["error_code"] == "provider_unavailable"


def test_web_search_handles_invalid_provider_json() -> None:
    provider = MagicMock()
    provider.search.return_value = "not-json"
    func = _get_web_search_func()

    with patch("server.tools.web_search.get_provider", return_value=provider):
        data = json.loads(func("query"))

    assert data["error_code"] == "invalid_provider_response"


def test_real_duckduckgo_integration_via_tool() -> None:
    register("duckduckgo", DuckDuckGoProvider())

    with patch("server.providers.duckduckgo.DDGS") as mock_ddgs:
        instance = mock_ddgs.return_value.__enter__.return_value
        instance.text.return_value = [
            {"title": "D1", "href": "https://d1.example", "body": "body1"},
        ]
        with patch("server.providers.duckduckgo._rate_limit"):
            data = json.loads(_get_web_search_func()("ddg test"))

    assert data["query"] == "ddg test"
    assert data["result_count"] == 1
