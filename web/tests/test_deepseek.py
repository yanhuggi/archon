"""Tests for server.providers.deepseek — DeepSeek search provider."""

import json
import os
from unittest.mock import patch, MagicMock

import httpx
import pytest

from server.providers.deepseek import DeepSeekProvider


@pytest.fixture
def provider() -> DeepSeekProvider:
    return DeepSeekProvider()


def _mock_http_client() -> MagicMock:
    """Create a mock HTTP client."""
    mock_client = MagicMock()
    mock_client.is_closed = False
    return mock_client


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


def test_search_no_api_key_returns_error_json(provider: DeepSeekProvider) -> None:
    """When DEEPSEEK_API_KEY is not set, returns JSON with error."""
    result = provider.search("test")
    data = json.loads(result)
    assert data["query"] == "test"
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["error"] == "DEEPSEEK_API_KEY not set"


# ---------------------------------------------------------------------------
# Successful search
# ---------------------------------------------------------------------------


def _set_api_key() -> None:
    os.environ["DEEPSEEK_API_KEY"] = "test-key-456"


def test_search_success(provider: DeepSeekProvider, deepseek_api_response: dict) -> None:
    """Successful search returns formatted results with summary."""
    _set_api_key()

    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = deepseek_api_response

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test query")
        data = json.loads(result)

    assert data["query"] == "test query"
    assert len(data["results"]) == 2
    assert data["result_count"] == 2
    assert data["summary"] == "Here is the answer to your search query."

    r0 = data["results"][0]
    assert r0["title"] == "DeepSeek Result 1"
    assert r0["url"] == "https://deepseek-example.com/1"


def test_search_sends_correct_payload(provider: DeepSeekProvider) -> None:
    """Verify the JSON payload sent to DeepSeek API (OpenAI format)."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = {"choices": []}

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        provider.search("hello", max_results=5, model="deepseek-v4-flash")

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["model"] == "deepseek-v4-flash"
        assert body["max_tokens"] == 8192
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] != ""
        assert body["messages"][1] == {"role": "user", "content": "hello"}
        assert "tools" in body

        headers = call_kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-key-456"


def test_search_custom_base_url(provider: DeepSeekProvider) -> None:
    """DEEPSEEK_BASE_URL overrides the default endpoint."""
    _set_api_key()
    custom_base = "https://custom.deepseek.com"
    expected_url = f"{custom_base}/v1/chat/completions"
    with patch.dict(os.environ, {"DEEPSEEK_BASE_URL": custom_base}):
        mock_client = _mock_http_client()
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {"choices": []}

        with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
            provider.search("test")
            called_url = mock_client.post.call_args[0][0]
            assert called_url == expected_url


# ---------------------------------------------------------------------------
# Thinking config
# ---------------------------------------------------------------------------


def test_thinking_disabled_by_default(provider: DeepSeekProvider) -> None:
    """With ARCHON_WEB_DEEPSEEK_THINKING not set, thinking is disabled."""
    config = provider._thinking_config()  # noqa: SLF001
    assert config == {"type": "disabled"}


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("true", "enabled"),
        ("TRUE", "enabled"),
        ("1", "enabled"),
        ("yes", "enabled"),
        ("false", "disabled"),
        ("0", "disabled"),
        ("", "disabled"),
    ],
)
def test_thinking_config_variants(env_value: str, expected: str) -> None:
    """Various values of ARCHON_WEB_DEEPSEEK_THINKING."""
    provider = DeepSeekProvider()
    with patch.dict(os.environ, {"ARCHON_WEB_DEEPSEEK_THINKING": env_value}):
        config = provider._thinking_config()  # noqa: SLF001
        assert config == {"type": expected}


def test_thinking_enabled_in_body(provider: DeepSeekProvider) -> None:
    """When thinking is enabled, the body includes a thinking key."""
    _set_api_key()
    with patch.dict(os.environ, {"ARCHON_WEB_DEEPSEEK_THINKING": "true"}):
        mock_client = _mock_http_client()
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {"content": []}

        with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
            provider.search("test")
            body = mock_client.post.call_args[1]["json"]
            assert "thinking" in body
            assert body["thinking"] == {"type": "enabled"}


def test_thinking_disabled_in_body(provider: DeepSeekProvider) -> None:
    """When thinking is disabled, the body does NOT include a thinking key."""
    _set_api_key()
    with patch.dict(os.environ, {"ARCHON_WEB_DEEPSEEK_THINKING": "false"}):
        mock_client = _mock_http_client()
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {"content": []}

        with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
            provider.search("test")
            body = mock_client.post.call_args[1]["json"]
            assert "thinking" not in body


# ---------------------------------------------------------------------------
# Edge cases: response parsing
# ---------------------------------------------------------------------------


def test_search_empty_content(provider: DeepSeekProvider) -> None:
    """OpenAI response with no choices produces empty results."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = {"choices": []}

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert data["result_count"] == 0
    assert "summary" not in data


def test_search_only_text_no_web_results(provider: DeepSeekProvider) -> None:
    """OpenAI response with text but no tool calls or annotations."""
    _set_api_key()
    resp = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Just an answer, no sources.",
                },
            }
        ]
    }
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = resp

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert data["result_count"] == 0
    assert data["summary"] == "Just an answer, no sources."


def test_search_only_web_results_no_text(provider: DeepSeekProvider) -> None:
    """OpenAI response with annotations but no text answer."""
    _set_api_key()
    resp = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "annotations": [
                        {
                            "type": "web_search_result",
                            "title": "Only Result",
                            "url": "https://only.com",
                        },
                    ],
                },
            }
        ]
    }
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = resp

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert data["result_count"] == 1
    assert "summary" not in data


def test_search_missing_title_and_url(provider: DeepSeekProvider) -> None:
    """Annotations handle missing title/url gracefully."""
    _set_api_key()
    resp = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "annotations": [
                        {"type": "web_search_result", "title": None, "url": ""},
                    ],
                },
            }
        ]
    }
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = resp

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert data["results"][0]["title"] == ""
    assert data["results"][0]["url"] == ""


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


def test_search_http_401(provider: DeepSeekProvider) -> None:
    """HTTP 401 returns error JSON."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=httpx.Request("POST", "https://api.deepseek.com/..."),
        response=httpx.Response(401, text="Unauthorized"),
    )

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "401" in data["error"]


def test_search_http_500(provider: DeepSeekProvider) -> None:
    """HTTP 500 returns error JSON."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.side_effect = httpx.HTTPStatusError(
        "500 Server Error",
        request=httpx.Request("POST", "https://api.deepseek.com/..."),
        response=httpx.Response(500, text="Internal Server Error"),
    )

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "500" in data["error"]


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------


def test_search_timeout(provider: DeepSeekProvider) -> None:
    """Timeout triggers RequestError handling."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.side_effect = httpx.TimeoutException(
        "Connection timed out",
        request=httpx.Request("POST", "https://api.deepseek.com/..."),
    )

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "Request failed" in data["error"]


def test_search_connection_error(provider: DeepSeekProvider) -> None:
    """Connection error returns error JSON."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.side_effect = httpx.RequestError(
        "Connection refused",
        request=httpx.Request("POST", "https://api.deepseek.com/..."),
    )

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "Request failed" in data["error"]


# ---------------------------------------------------------------------------
# Unexpected exception
# ---------------------------------------------------------------------------


def test_search_unexpected_exception(provider: DeepSeekProvider) -> None:
    """Any other exception is caught and returned as JSON error."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.side_effect = RuntimeError("Something went wrong")

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("test")
        data = json.loads(result)

    assert "error" in data
    assert "RuntimeError" in data["error"]


# ---------------------------------------------------------------------------
# Output JSON schema
# ---------------------------------------------------------------------------


def test_output_json_schema(provider: DeepSeekProvider) -> None:
    """Verify the structure of the output JSON."""
    _set_api_key()
    mock_client = _mock_http_client()
    mock_client.post.return_value.status_code = 200
    mock_client.post.return_value.json.return_value = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Summary.",
                    "annotations": [
                        {
                            "type": "web_search_result",
                            "title": "R1",
                            "url": "https://r1.com",
                        },
                    ],
                },
            },
        ],
    }

    with patch("server.providers.deepseek.get_shared_http_client", return_value=mock_client):
        result = provider.search("schema test")
        data = json.loads(result)

    assert "query" in data
    assert "results" in data
    assert "result_count" in data
    assert "summary" in data
    assert isinstance(data["results"], list)
    assert isinstance(data["result_count"], int)
    for r in data["results"]:
        assert "title" in r
        assert "url" in r
