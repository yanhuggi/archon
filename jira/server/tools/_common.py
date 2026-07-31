"""Shared response helpers for Jira MCP tools."""

from __future__ import annotations

import json
from typing import Any

from server.providers import get_provider


def error_response(code: str, message: str, **context: Any) -> str:
    return json.dumps({**context, "error": message, "error_code": code}, ensure_ascii=False)


def provider_or_error(name: str):
    try:
        return get_provider(name), None
    except ValueError as exc:
        return None, error_response("provider_unavailable", str(exc))


def ensure_json_result(raw: object, **defaults: Any) -> str:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return error_response(
                "invalid_provider_response",
                "Jira provider returned invalid JSON",
                **defaults,
            )
    elif isinstance(raw, dict):
        data = raw
    else:
        return error_response(
            "invalid_provider_response",
            "Jira provider returned an unsupported response",
            **defaults,
        )
    if not isinstance(data, dict):
        return error_response(
            "invalid_provider_response",
            "Jira provider response must be an object",
            **defaults,
        )
    for key, value in defaults.items():
        data.setdefault(key, value)
    if "error" in data:
        data.setdefault("error_code", "provider_error")
    return json.dumps(data, ensure_ascii=False)


def ensure_markdown_result(raw: object, *, issue_key: str) -> str:
    if not isinstance(raw, str):
        return error_response(
            "invalid_provider_response",
            "Jira provider returned an unsupported response",
            issue_key=issue_key,
        )
    stripped = raw.strip()
    if stripped.startswith("{"):
        return ensure_json_result(raw, issue_key=issue_key)
    if stripped.startswith("Error:"):
        return error_response("provider_error", stripped.removeprefix("Error:").strip(), issue_key=issue_key)
    return raw
