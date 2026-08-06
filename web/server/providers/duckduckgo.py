"""No-key public web search provider built on the ``ddgs`` package."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator
from urllib.parse import quote, quote_plus, urlparse

from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException

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

# ``ddgs`` 9.14.4 defines RatelimitException but never raises it: engine errors
# are collected into one variable and re-raised as a bare DDGSException, and the
# HTTP layer maps everything except primp.TimeoutError to DDGSException too.
# Classifying a failure therefore has to fall back to the message text.
#
# That text embeds the request URL, which embeds the user's query, so a bare
# substring search lets a query term impersonate an upstream signal: searching
# for "HTTP 429 troubleshooting" made an ordinary TLS failure look throttled.
# _redact_error_text strips the URL and query first, and the patterns below
# additionally require real status context rather than a loose number.
_RATE_LIMIT_PATTERNS = (
    re.compile(r"too\s+many\s+requests"),
    re.compile(r"rate[\s_-]*limit"),
    # "HTTP 429", "status: 429", "code=429" — but not a bare "429".
    re.compile(r"\b(?:http|https|status|statuscode|code|error|response)\W{0,4}429\b"),
    re.compile(r"\b429\W{1,4}(?:too|client|error|retry)"),
)
# ddgs only converts a timeout to TimeoutException when the aggregated error
# text happens to contain "timed out", so a "timeout" wording arrives as a
# plain DDGSException. Match both spellings.
_TIMEOUT_PATTERNS = (
    re.compile(r"timed[\s_-]*out"),
    re.compile(r"\btimeout\b"),
)
# Matches the URL an upstream error quotes, including the query string.
_URL_PATTERN = re.compile(r"\b(?:https?|ftp)://\S*", re.IGNORECASE)

# Han blocks that justify Chinese-localized results. The original single range
# missed compatibility ideographs and every supplementary-plane extension, so
# queries written with those characters silently fell back to ``us-en``.
_HAN_RANGES = (
    ("\u3400", "\u4dbf"),  # Extension A
    ("\u4e00", "\u9fff"),  # CJK Unified Ideographs
    ("\uf900", "\ufaff"),  # Compatibility Ideographs
    ("\U00020000", "\U0003ffff"),  # Extensions B-I (supplementary planes)
)
# Kana implies Japanese. Those queries contain Han characters too, so without
# this check a Japanese query would be routed to the Chinese region.
_KANA_RANGES = (
    ("\u3040", "\u30ff"),  # Hiragana and Katakana
    ("\u31f0", "\u31ff"),  # Katakana Phonetic Extensions
    ("\uff66", "\uff9d"),  # Half-width katakana, as produced by legacy IMEs
    # Supplementary-plane kana. These sit inside the Han Extension B-I range
    # below, so without an explicit entry archaic or small kana would select the
    # Chinese region.
    ("\U0001b000", "\U0001b12f"),  # Kana Supplement and Kana Extended-A
    ("\U0001b130", "\U0001b16f"),  # Small Kana Extension and Kana Extended-B
)


def _in_ranges(char: str, ranges: tuple[tuple[str, str], ...]) -> bool:
    return any(low <= char <= high for low, high in ranges)


def _region_for_query(query: str) -> str:
    """Use Chinese-localized results when the query contains Han text."""

    if any(_in_ranges(char, _KANA_RANGES) for char in query):
        return "us-en"
    if any(_in_ranges(char, _HAN_RANGES) for char in query):
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


def _redact_error_text(text: str, query: str) -> str:
    """Remove caller-controlled substrings before classifying an error message.

    Upstream errors quote the request URL, which contains the percent-encoded
    query, so the user's own words end up inside the text used for matching.
    Strip URLs and the query itself so a search for "HTTP 429 troubleshooting"
    cannot make an unrelated connection failure look like throttling.
    """

    redacted = _URL_PATTERN.sub(" ", text)
    # The query may also appear outside a URL, and engines may quote it in
    # either raw or percent-encoded form.
    candidates = {query, quote_plus(query), quote(query)}
    for candidate in candidates:
        stripped = candidate.strip()
        if len(stripped) >= 3:
            redacted = redacted.replace(stripped, " ")
    return redacted


def _classify_upstream_failure(query: str, exc: BaseException, timeout: int) -> str:
    """Map an upstream failure to the narrowest documented error code.

    ``ddgs`` flattens engine failures into ``DDGSException`` with the original
    error only present as text, so the exception type alone cannot distinguish
    throttling from a dead connection. Match the message only after redacting
    the parts the caller controls.
    """

    parts: list[str] = []
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    # Walk the chain: the HTTP layer raises DDGSException *from* the original
    # error, so both the typed exception and any status context can live on
    # __cause__ rather than on the outermost error. Guard against a cycle.
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    text = _redact_error_text(" ".join(parts), query).lower()

    # A typed error anywhere in the chain is authoritative: it carries no
    # caller-controlled text, so it cannot be spoofed by the query.
    if any(isinstance(item, RatelimitException) for item in chain) or any(
        p.search(text) for p in _RATE_LIMIT_PATTERNS
    ):
        LOGGER.warning("Web search upstream rate-limited the request: %s", exc)
        return _error_response(
            query,
            f"Web search provider rate-limited the request: {exc}",
            "rate_limited",
        )
    if any(isinstance(item, TimeoutException) for item in chain) or any(
        p.search(text) for p in _TIMEOUT_PATTERNS
    ):
        LOGGER.warning("Web search upstream timed out after %ss: %s", timeout, exc)
        return _error_response(
            query,
            f"Web search provider timed out after {timeout}s: {exc}",
            "upstream_timeout",
        )
    LOGGER.error("Web search provider failed: %s", exc)
    return _error_response(query, f"Web search provider failed: {exc}", "upstream_error")


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

    No API key is required. Request spacing, timeout, and proxy settings come
    from :class:`server.config.WebConfig`. Pass a config to bind this provider
    to fixed settings; omit it to re-read ``ARCHON_WEB_*`` on every search.
    """

    def __init__(self, config: WebConfig | None = None) -> None:
        self._config = config

    def _active_config(self) -> WebConfig:
        # A provider built with an explicit config must keep using it. Reading
        # the environment here instead would silently discard CLI overrides and
        # any per-server configuration the caller chose.
        if self._config is not None:
            return self._config
        return WebConfig.from_env()

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

        if isinstance(max_results, bool):
            return _error_response(
                normalized_query, "max_results must be an integer", "invalid_max_results"
            )
        try:
            normalized_limit = min(max(int(max_results), 1), MAX_RESULTS)
        except (TypeError, ValueError, OverflowError):
            return _error_response(normalized_query, "max_results must be an integer", "invalid_max_results")

        config = self._active_config()
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
            return _classify_upstream_failure(normalized_query, exc, config.timeout)


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
