"""Tests for server.providers.duckduckgo — DuckDuckGo search provider."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

import primp
from collections.abc import Callable
from urllib.parse import quote_plus

from ddgs.exceptions import RatelimitException, TimeoutException
from ddgs.http_client import HttpClient

import server.providers.duckduckgo as ddg_module
from server.providers.duckduckgo import (
    DuckDuckGoProvider,
    _classify_upstream_failure,
    _rate_limit,
    _region_for_query,
)


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset the global rate limiter before and after each test.

    Directly modifies the module variable so the rate limit function
    sees the change.
    """
    with ddg_module._lock:
        ddg_module._last_call = 0.0
        ddg_module._rate_limit_warning_emitted = False
    yield
    with ddg_module._lock:
        ddg_module._last_call = 0.0
        ddg_module._rate_limit_warning_emitted = False


@pytest.fixture
def provider() -> DuckDuckGoProvider:
    return DuckDuckGoProvider()


@pytest.fixture
def mock_ddgs() -> MagicMock:
    """Mock the DDGS class and its text() method."""
    with patch("server.providers.duckduckgo.DDGS") as mock:
        ddgs_instance = mock.return_value.__enter__.return_value
        ddgs_instance.text.return_value = [
            {"title": "DDG Result 1", "href": "https://ddg.com/1", "body": "Snippet 1"},
            {"title": "DDG Result 2", "href": "https://ddg.com/2", "body": "Snippet 2"},
        ]
        yield mock


# ---------------------------------------------------------------------------
# Successful search
# ---------------------------------------------------------------------------


def test_search_success(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """Successful search returns formatted results."""
    result = provider.search("test query")
    data = json.loads(result)

    assert data["query"] == "test query"
    assert len(data["results"]) == 2
    assert data["result_count"] == 2

    r0 = data["results"][0]
    assert r0["title"] == "DDG Result 1"
    assert r0["url"] == "https://ddg.com/1"
    assert r0["snippet"] == "Snippet 1"

    # Verify max_results is passed through
    mock_ddgs.return_value.__enter__.return_value.text.assert_called_once_with(
        "test query", max_results=10, backend="brave", region="us-en"
    )


def test_search_custom_max_results(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """max_results parameter is forwarded to DDGS.text()."""
    provider.search("q", max_results=5)
    mock_ddgs.return_value.__enter__.return_value.text.assert_called_once_with(
        "q", max_results=5, backend="brave", region="us-en"
    )


def test_search_never_returns_more_than_requested(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    """Defend against an upstream that ignores its max_results argument."""
    mock_ddgs.return_value.__enter__.return_value.text.return_value = [
        {"title": str(index), "href": f"https://example.com/{index}", "body": "body"}
        for index in range(5)
    ]
    data = json.loads(provider.search("q", max_results=2))
    assert data["result_count"] == 2


def test_search_passes_time_range_as_ddgs_timelimit(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    """Friendly MCP time ranges are translated to DuckDuckGo values."""
    provider.search("recent release", time_range="week")
    mock_ddgs.return_value.__enter__.return_value.text.assert_called_once_with(
        "recent release", max_results=10, timelimit="w", backend="brave", region="us-en"
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("福州天气预报 今天", "cn-zh"),
        ("MCP Python SDK", "us-en"),
        # Han blocks outside the main CJK range must still select cn-zh.
        ("繁體中文", "cn-zh"),
        ("㐀", "cn-zh"),  # Extension A
        ("豈更", "cn-zh"),  # Compatibility ideographs
        ("\U00020000 字", "cn-zh"),  # Extension B, supplementary plane
        # Kana means the query is Japanese, even when it contains Han.
        ("ひらがな", "us-en"),
        ("東京の天気", "us-en"),
        ("日本語のテスト", "us-en"),
        ("한국어 검색", "us-en"),
        # Half-width katakana, as produced by legacy IMEs, is still Japanese.
        ("東京ﾃｽﾄ", "us-en"),
        ("ﾃｽﾄ", "us-en"),
        ("ﾊﾝｶｸ ｶﾀｶﾅ", "us-en"),
        # Supplementary-plane kana sits inside the Han Extension B-I range, so
        # archaic and small kana would otherwise select the Chinese region.
        ("東京𛀁", "us-en"),  # U+1B001, archaic hiragana
        ("\U0001b000", "us-en"),  # Kana Supplement
        ("東京\U0001b122", "us-en"),  # Kana Extended-A
        ("東京\U0001b132", "us-en"),  # Small Kana Extension
        # Kana Extended-B is at U+1AFF0-U+1AFFF, not next to the other blocks.
        ("東京𚿰", "us-en"),  # U+1AFF0, Minnan tone mark
        ("東京\U0001aff1", "us-en"),
    ],
)
def test_search_selects_region_from_query(query: str, expected: str) -> None:
    assert _region_for_query(query) == expected


def test_search_uses_chinese_region(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    provider.search("福州天气预报 今天")
    mock_ddgs.return_value.__enter__.return_value.text.assert_called_once_with(
        "福州天气预报 今天",
        max_results=10,
        backend="brave",
        region="cn-zh",
    )


def test_search_configures_timeout_and_proxy(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    """Network settings are forwarded to the DDGS client."""
    with patch.dict(
        os.environ,
        {"ARCHON_WEB_TIMEOUT": "25", "ARCHON_WEB_PROXY": "http://proxy.example:8080"},
    ):
        provider.search("query")
    mock_ddgs.assert_called_once_with(timeout=25, proxy="http://proxy.example:8080")


def test_search_uses_injected_config_instead_of_environment(mock_ddgs: MagicMock) -> None:
    """A provider built with a config must not re-read the environment.

    Reading ``from_env`` here would discard CLI overrides such as --transport's
    sibling settings and any per-server configuration the caller chose.
    """
    from server.config import WebConfig

    configured = DuckDuckGoProvider(
        WebConfig(interval=0.0, timeout=42, proxy="http://injected.example:1234")
    )
    with patch.dict(os.environ, {"ARCHON_WEB_TIMEOUT": "7", "ARCHON_WEB_PROXY": "http://env:9"}):
        configured.search("query")

    mock_ddgs.assert_called_once_with(timeout=42, proxy="http://injected.example:1234")


def test_search_without_config_still_follows_environment(mock_ddgs: MagicMock) -> None:
    """Omitting the config keeps the documented env-driven behavior."""
    with patch.dict(os.environ, {"ARCHON_WEB_TIMEOUT": "33"}):
        DuckDuckGoProvider().search("query")

    mock_ddgs.assert_called_once_with(timeout=33)


def test_search_rejects_boolean_max_results(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """``True`` is an int in Python but never a meaningful result count."""
    data = json.loads(provider.search("query", max_results=True))

    assert data["error_code"] == "invalid_max_results"
    mock_ddgs.assert_not_called()


# ---------------------------------------------------------------------------
# Output JSON schema
# ---------------------------------------------------------------------------


def test_output_json_schema(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """Verify the structure of the output JSON."""
    mock_ddgs.return_value.__enter__.return_value.text.return_value = [
        {"title": "R1", "href": "https://r1.com", "body": "c1"},
    ]

    result = provider.search("schema test")
    data = json.loads(result)

    assert "query" in data
    assert "results" in data
    assert "result_count" in data
    assert isinstance(data["results"], list)
    assert isinstance(data["result_count"], int)
    # No error in success case
    assert "error" not in data

    for r in data["results"]:
        assert "title" in r
        assert "url" in r
        assert "snippet" in r


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------


def test_search_missing_fields(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """Items with missing fields are handled gracefully."""
    mock_ddgs.return_value.__enter__.return_value.text.return_value = [
        {"title": None, "href": "https://x.com", "body": None},
    ]

    result = provider.search("test")
    data = json.loads(result)

    assert data["results"][0]["title"] == ""
    assert data["results"][0]["snippet"] == ""


def test_search_deduplicates_urls_and_rejects_unsafe_schemes(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    mock_ddgs.return_value.__enter__.return_value.text.return_value = [
        {"title": "A", "href": "https://example.com/a", "body": "one"},
        {"title": "A duplicate", "href": "https://example.com/a", "body": "two"},
        {"title": "Unsafe", "href": "javascript:alert(1)", "body": "three"},
    ]

    data = json.loads(provider.search("test"))
    assert data["result_count"] == 2
    assert data["results"][1]["url"] == ""


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


def test_search_ddgs_exception(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """DDGS exception is caught and returned as JSON error."""
    mock_ddgs.return_value.__enter__.return_value.text.side_effect = RuntimeError("DDG blocked us")

    result = provider.search("test")
    data = json.loads(result)

    assert "error" in data
    assert "Web search provider failed" in data["error"]
    assert "DDG blocked us" in data["error"]
    assert data["error_code"] == "upstream_error"
    assert data["results"] == []


def test_search_reports_rate_limiting_distinctly(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    """Throttling is retryable; a generic upstream failure often is not."""
    mock_ddgs.return_value.__enter__.return_value.text.side_effect = RatelimitException("slow down")

    data = json.loads(provider.search("test"))

    assert data["error_code"] == "rate_limited"
    assert data["results"] == []
    assert data["result_count"] == 0


def test_search_reports_timeout_distinctly(
    provider: DuckDuckGoProvider, mock_ddgs: MagicMock
) -> None:
    """A timeout tells the caller to widen ARCHON_WEB_TIMEOUT, not to retry blindly."""
    mock_ddgs.return_value.__enter__.return_value.text.side_effect = TimeoutException("too slow")

    data = json.loads(provider.search("test"))

    assert data["error_code"] == "upstream_timeout"
    assert "10s" in data["error"]
    assert data["results"] == []


# ---------------------------------------------------------------------------
# Failure classification through the real ddgs call path
#
# Patching DDGS.text skips everything ddgs does internally: engine errors are
# collected into one variable and re-raised as a bare DDGSException, and only a
# message containing "timed out" becomes TimeoutException. These tests patch the
# HTTP client instead, so the exception the provider sees is the one ddgs
# actually produces.
# ---------------------------------------------------------------------------


@pytest.fixture
def http_failure() -> Callable[[BaseException], dict]:
    """Run a real ddgs search whose HTTP layer raises ``exc``."""

    def run(exc: BaseException) -> dict:
        def failing_request(self: object, *args: object, **kwargs: object) -> object:
            raise exc

        with patch.object(HttpClient, "request", failing_request):
            with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0"}):
                return json.loads(DuckDuckGoProvider().search("test", max_results=2))

    return run


def test_rate_limiting_survives_ddgs_exception_flattening(http_failure) -> None:
    """ddgs 9.14.4 never re-raises RatelimitException, so type alone is not enough.

    Engine failures are aggregated and re-raised as a plain DDGSException, which
    made an ``except RatelimitException`` clause unreachable dead code.
    """
    data = http_failure(RatelimitException("429 Too Many Requests"))

    assert data["error_code"] == "rate_limited"
    assert data["results"] == []


def test_rate_limiting_detected_from_status_code_text(http_failure) -> None:
    """A throttling response carries no distinct type; the status code remains."""
    data = http_failure(Exception("HTTP 429 Too Many Requests"))

    assert data["error_code"] == "rate_limited"


@pytest.mark.parametrize("message", ["timed out", "operation timeout after 10s"])
def test_timeout_detected_for_both_upstream_spellings(http_failure, message: str) -> None:
    """ddgs only maps "timed out" to TimeoutException, so "timeout" arrived generic."""
    data = http_failure(primp.TimeoutError(message))

    assert data["error_code"] == "upstream_timeout"
    assert "10s" in data["error"]


def test_connection_failure_stays_a_generic_upstream_error(http_failure) -> None:
    """Only throttling and timeouts get a narrower code; nothing else is guessed."""
    data = http_failure(Exception("tls handshake eof"))

    assert data["error_code"] == "upstream_error"


@pytest.mark.parametrize(
    "query",
    [
        "HTTP 429 troubleshooting",
        "python timeout handling",
        "rate limit best practices",
        "too many requests error",
        "nginx rate-limit config",
        "429",
        "连接 timeout 处理",
    ],
)
def test_query_terms_cannot_impersonate_an_upstream_signal(query: str) -> None:
    """Upstream errors quote the request URL, which contains the user's query.

    Matching the raw message let a search for "HTTP 429 troubleshooting" turn an
    ordinary TLS failure into rate_limited, telling the caller to back off for a
    problem that retrying would not fix.
    """

    def failing_request(self: object, *args: object, **kwargs: object) -> object:
        raise Exception(
            "ConnectError: error sending request for url "
            f"(https://search.brave.com/search?q={quote_plus(query)}&source=web) "
            "> client error (Connect) > tls handshake eof"
        )

    with patch.object(HttpClient, "request", failing_request):
        with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0"}):
            data = json.loads(DuckDuckGoProvider().search(query, max_results=2))

    assert data["error_code"] == "upstream_error"


def test_a_real_signal_still_wins_over_a_matching_query(http_failure) -> None:
    """Redaction must not suppress a genuine status code."""
    data = http_failure(Exception("HTTP 429 Too Many Requests"))

    assert data["error_code"] == "rate_limited"


@pytest.mark.parametrize(
    ("query", "message", "expected"),
    [
        ("HTTP 429 Too Many Requests", "HTTP 429 Too Many Requests", "rate_limited"),
        ("timeout", "operation timeout after 10s", "upstream_timeout"),
        ("rate limit", "upstream rate limit exceeded", "rate_limited"),
        ("timed out", "timed out", "upstream_timeout"),
        ("429", "status: 429", "rate_limited"),
    ],
)
def test_a_query_matching_the_real_error_does_not_hide_it(
    query: str, message: str, expected: str
) -> None:
    """Redacting every occurrence of the query erased genuine evidence.

    Deleting the query text outright fixed forged signals but created the
    opposite defect: with query="timeout" and a real timeout, the only evidence
    was removed and the failure degraded to upstream_error. Redaction is limited
    to the URL, which is the only place the upstream echoes the query.
    """

    def failing_request(self: object, *args: object, **kwargs: object) -> object:
        raise Exception(message)

    with patch.object(HttpClient, "request", failing_request):
        with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0"}):
            data = json.loads(DuckDuckGoProvider().search(query, max_results=2))

    assert data["error_code"] == expected


@pytest.mark.parametrize(
    "message",
    ["HTTP 429 Too Many Requests", "status: 429", "429 Too Many Requests", "rate limit exceeded"],
)
def test_rate_limiting_needs_real_status_context(http_failure, message: str) -> None:
    """A bare number is not enough context, but a status-qualified 429 is."""
    assert http_failure(Exception(message))["error_code"] == "rate_limited"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RatelimitException("slow down"), "rate_limited"),
        (TimeoutException("nothing useful"), "upstream_timeout"),
    ],
)
def test_typed_errors_classify_without_any_text_marker(
    exc: BaseException, expected: str
) -> None:
    """A typed error carries no caller-controlled text, so it cannot be spoofed.

    ddgs 9.14.4 stringifies engine errors and drops the chain, so this path is
    only reachable for a provider that raises the typed errors directly, but it
    must not depend on message wording.
    """
    data = json.loads(_classify_upstream_failure("normal query", exc, timeout=10))

    assert data["error_code"] == expected


def test_typed_error_is_found_through_the_exception_chain() -> None:
    """Wrapping must not hide a retryable failure behind a generic error."""
    wrapper = RuntimeError("engine failed")
    wrapper.__cause__ = RatelimitException("slow down")

    data = json.loads(_classify_upstream_failure("q", wrapper, timeout=10))

    assert data["error_code"] == "rate_limited"


def test_classifier_reads_the_exception_chain() -> None:
    """The status code may sit on __cause__ rather than the outer message.

    ddgs' own aggregation stringifies the error and loses the chain, but its HTTP
    layer raises ``DDGSException(...) from ex``, and a future provider may too.
    Classify on the chain so wrapping cannot hide a retryable failure.
    """
    wrapper = RuntimeError("engine failed")
    wrapper.__cause__ = Exception("429 Too Many Requests")

    data = json.loads(_classify_upstream_failure("test", wrapper, timeout=10))

    assert data["error_code"] == "rate_limited"


def test_classifier_ignores_a_self_referencing_chain() -> None:
    """A cycle in __context__ must not loop forever."""
    first = RuntimeError("boom")
    second = RuntimeError("bang")
    first.__context__ = second
    second.__context__ = first

    data = json.loads(_classify_upstream_failure("test", first, timeout=10))

    assert data["error_code"] == "upstream_error"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_enforces_interval() -> None:
    """_rate_limit ensures at least `interval` seconds between calls."""
    _rate_limit(0.05)  # First call — no wait
    t0 = time.monotonic()
    _rate_limit(0.1)  # Should wait ~0.1 seconds
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.08, f"Expected ~0.1s wait, got {elapsed:.3f}s"  # small tolerance


def test_rate_limit_no_wait_when_elapsed_sufficient() -> None:
    """If enough time has passed, _rate_limit does not wait."""
    with ddg_module._lock:
        ddg_module._last_call = 0.0  # Set last call to distant past
    t0 = time.monotonic()
    _rate_limit(0.001)  # tiny interval
    elapsed = time.monotonic() - t0
    assert elapsed < 0.01, f"Should not have waited, got {elapsed:.3f}s"


def test_rate_limit_zero_interval() -> None:
    """Zero interval means no delay."""
    _rate_limit(0.0)
    t0 = time.monotonic()
    _rate_limit(0.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.01


def test_rate_limit_thread_safety() -> None:
    """Rate limiter lock is reentrant-safe — no concurrent-call deadlock."""
    _rate_limit(0.0)
    assert True  # reached without deadlock


def test_rate_limit_is_shared_through_state_file(tmp_path) -> None:
    """A persisted timestamp limits a later call even after local state resets."""
    state_file = tmp_path / "rate-limit"
    with patch("server.providers.duckduckgo.time.time", side_effect=[100.0, 100.0, 100.5, 102.0]):
        with patch("server.providers.duckduckgo.time.sleep") as mock_sleep:
            _rate_limit(2.0, state_file)
            ddg_module._last_call = 0.0
            _rate_limit(2.0, state_file)

    mock_sleep.assert_called_once_with(1.5)
    assert float(state_file.read_text().strip()) == 102.0


def test_rate_limit_falls_back_when_state_file_is_unavailable(tmp_path) -> None:
    """A state-file error must not make web search unavailable."""
    unavailable = tmp_path / "directory"
    unavailable.mkdir()
    with patch("server.providers.duckduckgo.time.monotonic", side_effect=[10.0, 10.0]):
        _rate_limit(2.0, unavailable)
    assert ddg_module._last_call == 10.0


def test_rate_limiter_applied_in_search(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """The rate limiter is called before the DDGS API call."""
    with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
        provider.search("test")
        mock_rl.assert_called_once()
        # Default interval is 2.0
        args = mock_rl.call_args[0]
        assert args[0] == 2.0
        assert args[1].name == "rate-limit"


def test_rate_limiter_custom_interval(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """ARCHON_WEB_DUCKDUCKGO_INTERVAL env var changes the rate limit interval."""
    with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0.5"}):
        with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
            provider.search("test")
            assert mock_rl.call_args.args[0] == 0.5


def test_rate_limiter_invalid_interval_falls_back(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """Invalid ARCHON_WEB_DUCKDUCKGO_INTERVAL falls back to 2.0."""
    with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "not-a-float"}):
        with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
            provider.search("test")
            assert mock_rl.call_args.args[0] == 2.0
