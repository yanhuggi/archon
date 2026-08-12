"""Tavily-backed web search provider."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from server.config import WebConfig

LOGGER = logging.getLogger(__name__)
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESULTS = 20
MAX_TITLE_LENGTH = 300
MAX_FIELD_LENGTH = 2000


def _error_response(query: str, message: str, code: str) -> str:
    return json.dumps(
        {"query": query, "results": [], "result_count": 0, "error": message, "error_code": code},
        ensure_ascii=False,
    )


def _clean_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return f"{text[:limit]}..." if len(text) > limit else text


def _clean_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    parsed = urlparse(url)
    return url if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""


def _format_results(raw_results: object, limit: int) -> list[dict[str, str | float]]:
    if not isinstance(raw_results, Iterable) or isinstance(raw_results, (str, bytes, Mapping)):
        return []
    results: list[dict[str, str | float]] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        url = _clean_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        result: dict[str, str | float] = {
            "title": _clean_text(item.get("title"), MAX_TITLE_LENGTH),
            "url": url,
            "snippet": _clean_text(item.get("content"), MAX_FIELD_LENGTH),
        }
        if isinstance(item.get("score"), (int, float)) and not isinstance(item.get("score"), bool):
            result["score"] = float(item["score"])
        if item.get("published_date"):
            result["published_date"] = _clean_text(item.get("published_date"), 100)
        results.append(result)
        if len(results) >= limit:
            break
    return results


class TavilyProvider:
    """Search Tavily using ``TAVILY_API_KEY`` or an injected configuration."""

    def __init__(self, config: WebConfig | None = None) -> None:
        self._config = config

    def _active_config(self) -> WebConfig:
        return self._config if self._config is not None else WebConfig.from_env()

    def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        time_range: str | None = None,
        **_: object,
    ) -> str:
        normalized_query = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized_query:
            return _error_response("", "query must not be empty", "invalid_query")
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            return _error_response(normalized_query, "max_results must be an integer", "invalid_max_results")
        limit = min(max(max_results, 1), MAX_RESULTS)
        config = self._active_config()
        api_key = config.tavily_api_key
        if not api_key:
            return _error_response(normalized_query, "TAVILY_API_KEY is not configured", "missing_api_key")

        payload: dict[str, object] = {
            "api_key": api_key,
            "query": normalized_query,
            "search_depth": config.tavily_search_depth,
            "topic": config.tavily_topic,
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if time_range:
            payload["time_range"] = time_range
        request = Request(
            TAVILY_SEARCH_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "archon-web/0.1"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=config.timeout) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
            if not isinstance(data, Mapping):
                return _error_response(normalized_query, "Tavily returned invalid JSON", "invalid_provider_response")
            results = _format_results(data.get("results"), limit)
            return json.dumps({"query": normalized_query, "results": results, "result_count": len(results)}, ensure_ascii=False)
        except HTTPError as exc:
            code = "rate_limited" if exc.code == 429 else "upstream_error"
            message = "Tavily rate limit exceeded" if exc.code == 429 else f"Tavily HTTP error: {exc.code}"
            return _error_response(normalized_query, message, code)
        except TimeoutError:
            return _error_response(normalized_query, f"Tavily timed out after {config.timeout}s", "upstream_timeout")
        except json.JSONDecodeError:
            return _error_response(
                normalized_query, "Tavily returned invalid JSON", "invalid_provider_response"
            )
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                return _error_response(
                    normalized_query, f"Tavily timed out after {config.timeout}s", "upstream_timeout"
                )
            LOGGER.warning("Tavily search failed: %s", exc)
            return _error_response(normalized_query, f"Tavily search failed: {exc}", "upstream_error")
        except OSError as exc:
            LOGGER.warning("Tavily search failed: %s", exc)
            return _error_response(normalized_query, f"Tavily search failed: {exc}", "upstream_error")
