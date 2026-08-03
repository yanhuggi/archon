"""Shared fixtures for archon-vision tests."""

import json
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Clear provider env vars and registries before each test.

    This fixture runs automatically for every test,
    ensuring tests start from a clean environment.
    Only deletes the keys that archon-vision reads, leaving
    standard env vars (PATH, HOME, etc.) intact.
    Restores original values on teardown via monkeypatch.
    """
    from server.providers import _providers

    _providers.clear()
    for k in (
        "MIMO_API_KEY",
        "MIMO_BASE_URL",
        "MIMO_MODEL",
        "MIMO_TIMEOUT",
        "MIMO_MAX_TOKENS",
        "MIMO_MAX_IMAGE_MB",
        "MIMO_ALLOWED_DIR",
        "ARCHON_VISION_TRANSPORT",
        "ARCHON_VISION_HOST",
        "ARCHON_VISION_PORT",
        "ARCHON_VISION_LOG_LEVEL",
    ):
        monkeypatch.delenv(k, raising=False)

    yield
    _providers.clear()


@pytest.fixture
def mimo_api_response() -> dict:
    """Simulated MiMo API success response."""
    return {
        "id": "chatcmpl-test123",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": "这张图片展示了一片美丽的日落",
                    "role": "assistant",
                },
            }
        ],
        "created": 1776850561,
        "model": "mimo-v2.5",
        "object": "chat.completion",
        "usage": {
            "completion_tokens": 100,
            "prompt_tokens": 500,
            "total_tokens": 600,
        },
    }


@pytest.fixture
def mimo_api_response_with_reasoning() -> dict:
    """Simulated MiMo API success response with reasoning content."""
    return {
        "id": "chatcmpl-test456",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": "这张图片展示了一片美丽的日落",
                    "role": "assistant",
                    "reasoning_content": "用户展示了一张图片，我需要分析其内容...",
                },
            }
        ],
        "created": 1776850562,
        "model": "mimo-v2.5",
        "object": "chat.completion",
        "usage": {
            "completion_tokens": 120,
            "prompt_tokens": 500,
            "total_tokens": 620,
            "completion_tokens_details": {"reasoning_tokens": 50},
        },
    }
