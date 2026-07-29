"""Tavily search provider."""

import json
import os
import sys

import httpx

from server.providers._http import get_shared_http_client

TAVILY_API_URL = "https://api.tavily.com/search"

# Maximum character length for individual text fields (title, snippet).
# Prevents single-result fields from dominating the output.
_MAX_FIELD_LENGTH = 2000


class TavilyProvider:
    """Search provider backed by Tavily API."""

    def _api_url(self) -> str:
        return os.environ.get("TAVILY_API_URL") or TAVILY_API_URL

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return json.dumps({"query": query, "results": [], "result_count": 0, "error": "TAVILY_API_KEY not set"}, ensure_ascii=False)

        try:
            timeout = int(os.environ.get("ARCHON_WEB_TIMEOUT", "30"))
        except ValueError:
            print("Warning: invalid ARCHON_WEB_TIMEOUT value, falling back to 30s", file=sys.stderr)
            timeout = 30
        try:
            client = get_shared_http_client(timeout)
            resp = client.post(
                self._api_url(),
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": kwargs.get("search_depth", "basic"),
                    "max_results": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return self._format(query, data)

        except httpx.HTTPStatusError as e:
            print(f"Error: Tavily HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Tavily request failed: {e}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Tavily search failed: {e}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

    def _format(self, query: str, data: dict) -> str:
        raw = data.get("results", [])
        results = []
        for r in raw:
            raw_title = (r.get("title") or "").strip()
            raw_content = (r.get("content") or "").strip()
            item = {
                "title": raw_title[:_MAX_FIELD_LENGTH],
                "url": r.get("url", ""),
                "snippet": raw_content[:_MAX_FIELD_LENGTH] + (
                    "..." if len(raw_content) > _MAX_FIELD_LENGTH else ""
                ),
            }
            score = r.get("score")
            if score is not None:
                item["score"] = round(score, 2)
            results.append(item)

        return json.dumps({
            "query": query,
            "results": results,
            "result_count": len(results),
        }, ensure_ascii=False)
