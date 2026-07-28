"""Tests for server.providers.tavily — Tavily search provider."""

import json
import os
from unittest.mock import patch

import httpx
import pytest

from server.providers.tavily import TavilyProvider


@pytest.fixture
def provider() -> TavilyProvider:
    return TavilyProvider()


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


def test_search_no_api_key_returns_error_json(provider: TavilyProvider) -> None:
    """When TAVILY_API_KEY is not set, returns JSON with error."""
    result = provider.search("test")
    data = json.loads(result)
    assert data["query"] == "test"
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["error"] == "TAVILY_API_KEY not set"


# ---------------------------------------------------------------------------
# Successful search
# ---------------------------------------------------------------------------


def _set_api_key() -> None:
    os.environ["TAVILY_API_KEY"] = "test-key-123"


def test_search_success(provider: TavilyProvider, tavily_api_response: dict) -> None:
    """Successful search returns formatted results."""
    _set_api_key()

    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = tavily_api_response

        result = provider.search("test query")
        data = json.loads(result)

    assert data["query"] == "test query"
    assert len(data["results"]) == 2
    assert data["result_count"] == 2

    r0 = data["results"][0]
    assert r0["title"] == "Test Result 1"
    assert r0["url"] == "https://example.com/1"
    assert r0["snippet"] == "Snippet for result 1"
    assert r0["score"] == 0.95


def test_search_sends_correct_payload(provider: TavilyProvider) -> None:
    """Verify the JSON payload sent to Tavily API."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {"results": []}

        provider.search("hello", max_results=5, search_depth="advanced")

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["query"] == "hello"
        assert call_kwargs["json"]["max_results"] == 5
        assert call_kwargs["json"]["search_depth"] == "advanced"
        assert call_kwargs["json"]["api_key"] == "test-key-123"


def test_search_custom_base_url(provider: TavilyProvider) -> None:
    """TAVILY_API_URL overrides the default endpoint."""
    _set_api_key()
    with patch.dict(os.environ, {"TAVILY_API_URL": "https://custom.tavily.com/search"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = {"results": []}

            provider.search("test")
            called_url = mock_client.post.call_args[0][0]
            assert called_url == "https://custom.tavily.com/search"


# ---------------------------------------------------------------------------
# Edge cases: missing fields in API response
# ---------------------------------------------------------------------------


def test_search_missing_title_and_content(provider: TavilyProvider) -> None:
    """Items missing optional fields handle gracefully."""
    _set_api_key()
    api_response = {
        "results": [
            {"url": "https://x.com"},
            {"title": None, "url": "https://y.com", "content": None},
        ]
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = api_response

        result = provider.search("test")
        data = json.loads(result)

    assert data["result_count"] == 2
    assert data["results"][0]["title"] == ""
    assert data["results"][1]["snippet"] == ""


def test_search_no_results_key(provider: TavilyProvider) -> None:
    """API response missing 'results' key is handled."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {"not_results": []}

        result = provider.search("test")
        data = json.loads(result)

    assert data["result_count"] == 0


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


def test_search_http_401(provider: TavilyProvider) -> None:
    """HTTP 401 returns error JSON."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("POST", "https://api.tavily.com/search"),
            response=httpx.Response(401, text="Unauthorized"),
        )

        result = provider.search("test")
        data = json.loads(result)

    assert data["query"] == "test"
    assert "error" in data
    assert "401" in data["error"]


def test_search_http_500(provider: TavilyProvider) -> None:
    """HTTP 500 returns error JSON."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=httpx.Request("POST", "https://api.tavily.com/search"),
            response=httpx.Response(500, text="Internal Server Error"),
        )

        result = provider.search("test")
        data = json.loads(result)

    assert data["query"] == "test"
    assert "500" in data["error"]


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------


def test_search_timeout(provider: TavilyProvider) -> None:
    """Timeout triggers RequestError handling."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = httpx.TimeoutException(
            "Connection timed out", request=httpx.Request("POST", "https://api.tavily.com/search")
        )

        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "Request failed" in data["error"]


def test_search_connection_error(provider: TavilyProvider) -> None:
    """Connection error returns error JSON."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = httpx.RequestError(
            "Connection refused", request=httpx.Request("POST", "https://api.tavily.com/search")
        )

        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "Request failed" in data["error"]


# ---------------------------------------------------------------------------
# Unexpected exception
# ---------------------------------------------------------------------------


def test_search_unexpected_exception(provider: TavilyProvider) -> None:
    """Any other exception is caught and returned as JSON error."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.side_effect = RuntimeError("Something weird happened")

        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "RuntimeError" in data["error"]


# ---------------------------------------------------------------------------
# _format helper
# ---------------------------------------------------------------------------


def test_format_with_score(provider: TavilyProvider) -> None:
    """_format includes scores when present."""
    raw = {
        "results": [
            {"title": "A", "url": "https://a.com", "content": "desc a", "score": 0.876},
        ]
    }
    result = provider._format("q", raw)  # noqa: SLF001
    data = json.loads(result)
    assert data["results"][0]["score"] == 0.88  # rounded


def test_format_without_score(provider: TavilyProvider) -> None:
    """_format omits score when the field is missing."""
    raw = {
        "results": [
            {"title": "A", "url": "https://a.com", "content": "desc"},
        ]
    }
    result = provider._format("q", raw)  # noqa: SLF001
    data = json.loads(result)
    assert "score" not in data["results"][0]


def test_format_score_none(provider: TavilyProvider) -> None:
    """_format omits score when it's None."""
    raw = {
        "results": [
            {"title": "A", "url": "https://a.com", "content": "desc", "score": None},
        ]
    }
    result = provider._format("q", raw)  # noqa: SLF001
    data = json.loads(result)
    assert "score" not in data["results"][0]


# ---------------------------------------------------------------------------
# Output JSON schema
# ---------------------------------------------------------------------------


def test_output_json_schema(provider: TavilyProvider) -> None:
    """Verify the structure of the output JSON."""
    _set_api_key()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "results": [
                {"title": "R1", "url": "https://r1.com", "content": "c1"},
            ]
        }

        result = provider.search("schema test")
        data = json.loads(result)

    assert "query" in data
    assert "results" in data
    assert "result_count" in data
    assert isinstance(data["results"], list)
    assert isinstance(data["result_count"], int)
    for r in data["results"]:
        assert "title" in r
        assert "url" in r
        assert "snippet" in r
