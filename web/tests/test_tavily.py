from __future__ import annotations

import json
from urllib.error import HTTPError
from unittest.mock import patch

from server.config import WebConfig
from server.providers.tavily import TavilyProvider


def test_missing_api_key_returns_stable_error() -> None:
    data = json.loads(TavilyProvider(WebConfig()).search("python"))
    assert data["error_code"] == "missing_api_key"
    assert data["results"] == []


def test_blank_api_key_is_treated_as_unconfigured() -> None:
    with patch.dict("os.environ", {"TAVILY_API_KEY": "   "}, clear=True):
        assert WebConfig.from_env().tavily_api_key is None


def test_search_posts_tavily_payload_and_normalizes_results() -> None:
    config = WebConfig(
        timeout=7,
        tavily_api_key="tvly-test",
        tavily_search_depth="advanced",
        tavily_topic="news",
    )
    response = {
        "results": [
            {"title": " A title ", "url": "https://example.com", "content": "A snippet", "score": 0.9}
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(response).encode()

    with patch("server.providers.tavily.urlopen", return_value=FakeResponse()) as opener:
        data = json.loads(configured := TavilyProvider(config).search("  python   news ", max_results=3, time_range="day"))

    request = opener.call_args.args[0]
    payload = json.loads(request.data)
    assert request.get_header("Authorization") == "Bearer tvly-test"
    assert "api_key" not in payload
    assert payload["query"] == "python news"
    assert payload["search_depth"] == "advanced"
    assert payload["topic"] == "news"
    assert payload["max_results"] == 3
    assert payload["time_range"] == "day"
    assert json.loads(configured)["results"][0]["url"] == "https://example.com"


def test_rate_limit_and_invalid_response_have_specific_codes() -> None:
    provider = TavilyProvider(WebConfig(tavily_api_key="tvly-test"))
    with patch(
        "server.providers.tavily.urlopen",
        side_effect=HTTPError("https://api.tavily.com/search", 429, "Too Many Requests", {}, None),
    ):
        assert json.loads(provider.search("python"))["error_code"] == "rate_limited"

    class InvalidResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"not JSON"

    with patch("server.providers.tavily.urlopen", return_value=InvalidResponse()):
        assert json.loads(provider.search("python"))["error_code"] == "invalid_provider_response"


def test_malformed_success_responses_are_not_empty_results() -> None:
    provider = TavilyProvider(WebConfig(tavily_api_key="tvly-test"))

    class Response:
        def __init__(self, body: object) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    for body in ({}, {"error": "invalid key"}, {"results": {}}):
        with patch("server.providers.tavily.urlopen", return_value=Response(body)):
            data = json.loads(provider.search("python"))
        assert data["error_code"] == "invalid_provider_response"


def test_nonempty_results_with_no_valid_web_result_are_not_empty_results() -> None:
    provider = TavilyProvider(WebConfig(tavily_api_key="tvly-test"))

    class Response:
        def __init__(self, body: object) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    for results in ([1, 2], [{}], [{"url": "javascript:alert(1)"}]):
        with patch("server.providers.tavily.urlopen", return_value=Response({"results": results})):
            data = json.loads(provider.search("python"))
        assert data["error_code"] == "invalid_provider_response"

    with patch("server.providers.tavily.urlopen", return_value=Response({"results": []})):
        data = json.loads(provider.search("python"))
    assert data == {"query": "python", "results": [], "result_count": 0}
