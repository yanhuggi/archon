"""No-key public web search provider built on the ``ddgs`` package."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator
from urllib.parse import urlparse

from ddgs import DDGS

from server.config import WebConfig


LOGGER = logging.getLogger(__name__)

# Keep individual fields bounded so a search cannot consume an unreasonable
# amount of the model context window.
MAX_TITLE_LENGTH = 300
MAX_FIELD_LENGTH = 2000
MAX_RESULTS = 20
# ``ddgs`` auto selection is randomized, making latency and relevance unstable.
SEARCH_BACKEND = "brave"
TIME_RANGE_TO_TIMELIMIT = {
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
}


def _region_for_query(query: str) -> str:
    """Use Chinese-localized results when the query contains Han text."""

    if any("\u3400" <= char <= "\u9fff" for char in query):
        return "cn-zh"
    return "us-en"


# The thread lock coordinates calls inside one server process. The state file
# and OS file lock below extend the same start-to-start interval across all
# archon-web processes belonging to the user.
_last_call = 0.0
_lock = threading.Lock()
_rate_limit_warning_emitted = False


def _rate_limit(interval: float, state_file: Path | None = None) -> None:
    """Apply a process-local or cross-process start-to-start interval."""

    global _last_call, _rate_limit_warning_emitted
    interval = max(0.0, interval)
    if interval == 0:
        return

    with _lock:
        if state_file is not None:
            try:
                _shared_rate_limit(interval, state_file)
                _last_call = time.monotonic()
                return
            except OSError as exc:
                if not _rate_limit_warning_emitted:
                    LOGGER.warning(
                        "Cross-process rate limiter unavailable at %s; "
                        "falling back to process-local limiting: %s",
                        state_file,
                        exc,
                    )
                    _rate_limit_warning_emitted = True

        elapsed = time.monotonic() - _last_call
        if elapsed < interval:
            time.sleep(interval - elapsed)
        _last_call = time.monotonic()


def _shared_rate_limit(interval: float, state_file: Path) -> None:
    """Serialize request start times using a small per-user state file."""

    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("a+", encoding="ascii") as state:
        with _exclusive_file_lock(state):
            last_call = _read_timestamp(state)
            now = time.time()
            elapsed = now - last_call
            if 0 <= elapsed < interval:
                time.sleep(interval - elapsed)

            state.seek(0)
            state.truncate()
            state.write(f"{time.time():.6f}\n")
            state.flush()


def _read_timestamp(state: IO[str]) -> float:
    state.seek(0)
    try:
        value = float(state.read(64).strip() or "0")
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


@contextmanager
def _exclusive_file_lock(state: IO[str]) -> Iterator[None]:
    """Lock one state file on POSIX and Windows without extra dependencies."""

    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        state.seek(0, os.SEEK_END)
        if state.tell() == 0:
            state.write("0\n")
            state.flush()
        state.seek(0)
        while True:
            try:
                msvcrt.locking(state.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.05)
        try:
            yield
        finally:
            state.seek(0)
            msvcrt.locking(state.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(state.fileno(), fcntl.LOCK_UN)


def _clean_text(value: object, limit: int) -> str:
    """Convert a provider field to compact, predictable text."""

    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _clean_url(value: object) -> str:
    """Only expose normal HTTP(S) links from third-party result data."""

    if not isinstance(value, str):
        return ""
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _error_response(query: str, message: str, code: str = "search_failed") -> str:
    return json.dumps(
        {
            "query": query,
            "results": [],
            "result_count": 0,
            "error": message,
            "error_code": code,
        },
        ensure_ascii=False,
    )


class DuckDuckGoProvider:
    """Search provider backed by a stable no-key ``ddgs`` text engine.

    No API key is required. Request spacing, timeout, and proxy settings are
    controlled by :class:`server.config.WebConfig` environment variables.
    """

    def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        time_range: str | None = None,
        **kwargs: object,
    ) -> str:
        """Search the public web and return a compact JSON result envelope."""

        normalized_query = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized_query:
            return _error_response("", "query must not be empty", "invalid_query")

        try:
            normalized_limit = min(max(int(max_results), 1), MAX_RESULTS)
        except (TypeError, ValueError, OverflowError):
            return _error_response(normalized_query, "max_results must be an integer", "invalid_max_results")

        config = WebConfig.from_env()
        _rate_limit(config.interval, config.rate_limit_file)

        text_kwargs: dict[str, object] = {"max_results": normalized_limit}
        timelimit = TIME_RANGE_TO_TIMELIMIT.get(str(time_range).lower()) if time_range else None
        if timelimit:
            text_kwargs["timelimit"] = timelimit

        try:
            ddgs_kwargs: dict[str, object] = {"timeout": config.timeout}
            if config.proxy:
                ddgs_kwargs["proxy"] = config.proxy
            text_kwargs.update(
                {
                    "backend": SEARCH_BACKEND,
                    "region": _region_for_query(normalized_query),
                }
            )
            with DDGS(**ddgs_kwargs) as ddgs:
                raw_results = ddgs.text(normalized_query, **text_kwargs)
                results = _format_results(raw_results, limit=normalized_limit)
            return json.dumps(
                {
                    "query": normalized_query,
                    "results": results,
                    "result_count": len(results),
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            LOGGER.error("Web search provider failed: %s", exc)
            return _error_response(
                normalized_query,
                f"Web search provider failed: {exc}",
                "upstream_error",
            )


def _format_results(raw_results: object, *, limit: int = MAX_RESULTS) -> list[dict[str, str]]:
    """Normalize, de-duplicate, and bound raw search results."""

    if not isinstance(raw_results, Iterable) or isinstance(raw_results, (str, bytes, Mapping)):
        return []

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            continue
        url = _clean_url(raw.get("href") or raw.get("url"))
        # Keep URL-less entries for compatibility with unusual providers, but
        # de-duplicate only when a usable URL is present.
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        results.append(
            {
                "title": _clean_text(raw.get("title"), MAX_TITLE_LENGTH),
                "url": url,
                "snippet": _clean_text(raw.get("body") or raw.get("snippet"), MAX_FIELD_LENGTH),
            }
        )
    return results[: min(max(limit, 1), MAX_RESULTS)]
