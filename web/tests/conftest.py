"""Shared fixtures for archon-web tests."""

import json
from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clear_env() -> Generator[None, None, None]:
    """Clear provider env vars before each test.

    This fixture runs automatically for every test,
    ensuring tests start from a clean environment.
    """
    from server.providers import _providers

    _providers.clear()
    with patch.dict("os.environ", clear=True):
        yield
    _providers.clear()


@pytest.fixture
def valid_json_response(tavily_api_response: dict) -> str:
    """Return a JSON string from the Tavily mock response."""
    return json.dumps(tavily_api_response)


@pytest.fixture
def tavily_api_response() -> dict:
    """Simulated Tavily API success response."""
    return {
        "results": [
            {
                "title": "Test Result 1",
                "url": "https://example.com/1",
                "content": "Snippet for result 1",
                "score": 0.95,
            },
            {
                "title": "Test Result 2",
                "url": "https://example.com/2",
                "content": "Snippet for result 2",
                "score": 0.85,
            },
        ]
    }


@pytest.fixture
def deepseek_api_response() -> dict:
    """Simulated DeepSeek API success response (OpenAI format)."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1777777777,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Here is the answer to your search query.",
                    "tool_calls": [],
                    "annotations": [
                        {
                            "type": "web_search_result",
                            "title": "DeepSeek Result 1",
                            "url": "https://deepseek-example.com/1",
                        },
                        {
                            "type": "web_search_result",
                            "title": "DeepSeek Result 2",
                            "url": "https://deepseek-example.com/2",
                        },
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }
