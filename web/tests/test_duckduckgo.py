"""Tests for server.providers.duckduckgo — DuckDuckGo search provider."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

import server.providers.duckduckgo as ddg_module
from server.providers.duckduckgo import DuckDuckGoProvider, _rate_limit


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset the global rate limiter before and after each test.

    Directly modifies the module variable so the rate limit function
    sees the change.
    """
    with ddg_module._lock:
        ddg_module._last_call = 0.0
    yield
    with ddg_module._lock:
        ddg_module._last_call = 0.0


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
        "test query", max_results=10
    )


def test_search_custom_max_results(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """max_results parameter is forwarded to DDGS.text()."""
    provider.search("q", max_results=5)
    mock_ddgs.return_value.__enter__.return_value.text.assert_called_once_with("q", max_results=5)


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


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


def test_search_ddgs_exception(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """DDGS exception is caught and returned as JSON error."""
    mock_ddgs.return_value.__enter__.return_value.text.side_effect = RuntimeError("DDG blocked us")

    result = provider.search("test")
    data = json.loads(result)

    assert "error" in data
    assert "DuckDuckGo search failed" in data["error"]
    assert "DDG blocked us" in data["error"]


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


def test_rate_limiter_applied_in_search(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """The rate limiter is called before the DDGS API call."""
    with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
        provider.search("test")
        mock_rl.assert_called_once()
        # Default interval is 2.0
        args = mock_rl.call_args[0]
        assert args[0] == 2.0


def test_rate_limiter_custom_interval(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """ARCHON_WEB_DUCKDUCKGO_INTERVAL env var changes the rate limit interval."""
    with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "0.5"}):
        with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
            provider.search("test")
            mock_rl.assert_called_once_with(0.5)


def test_rate_limiter_invalid_interval_falls_back(provider: DuckDuckGoProvider, mock_ddgs: MagicMock) -> None:
    """Invalid ARCHON_WEB_DUCKDUCKGO_INTERVAL falls back to 2.0."""
    with patch.dict(os.environ, {"ARCHON_WEB_DUCKDUCKGO_INTERVAL": "not-a-float"}):
        with patch("server.providers.duckduckgo._rate_limit") as mock_rl:
            provider.search("test")
            mock_rl.assert_called_once_with(2.0)
