"""DuckDuckGo search provider — no API key required."""

import json
import os
import sys
import threading
import time

from duckduckgo_search import DDGS

# Maximum character length for individual text fields (title, snippet).
_MAX_FIELD_LENGTH = 2000

# Rate limiter: shared across all instances (same process)
_last_call = 0.0
_lock = threading.Lock()


def _rate_limit(interval: float) -> None:
    """Ensure at least `interval` seconds between consecutive calls."""
    global _last_call
    with _lock:
        elapsed = time.monotonic() - _last_call
        if elapsed < interval:
            time.sleep(interval - elapsed)
        _last_call = time.monotonic()


class DuckDuckGoProvider:
    """Search provider backed by DuckDuckGo. No API key needed.

    Rate-limited to avoid being blocked by DuckDuckGo.
    Interval configured via ARCHON_WEB_DUCKDUCKGO_INTERVAL (default: 2.0s).
    """

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        try:
            interval = float(os.environ.get("ARCHON_WEB_DUCKDUCKGO_INTERVAL", "2.0"))
        except ValueError:
            print("Warning: invalid ARCHON_WEB_DUCKDUCKGO_INTERVAL value, falling back to 2.0s", file=sys.stderr)
            interval = 2.0

        try:
            _rate_limit(interval)

            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    raw_title = (r.get("title") or "").strip()
                    raw_body = (r.get("body") or "").strip()
                    results.append({
                        "title": raw_title[:_MAX_FIELD_LENGTH],
                        "url": r.get("href", ""),
                        "snippet": raw_body[:_MAX_FIELD_LENGTH] + (
                            "..." if len(raw_body) > _MAX_FIELD_LENGTH else ""
                        ),
                    })

            return json.dumps({
                "query": query,
                "results": results,
                "result_count": len(results),
            }, ensure_ascii=False)

        except Exception as e:
            print(f"Error: DuckDuckGo search failed: {e}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"DuckDuckGo search failed: {e}"}, ensure_ascii=False)
