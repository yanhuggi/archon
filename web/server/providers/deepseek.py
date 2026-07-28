"""DeepSeek search provider — OpenAI 兼容协议."""

import json
import os
import sys

import httpx

# DeepSeek 官方 BASE_URL（OpenAI 兼容）: https://api.deepseek.com
# 代码自动拼接 /v1/chat/completions 得到完整 API 路径。
# 可通过 DEEPSEEK_BASE_URL 环境变量覆盖。
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"

SEARCH_SYSTEM_PROMPT = (
    "You are a web search assistant. "
    "Search the web for the user's query and provide a comprehensive answer. "
    "Do not call web_search again after you have results. "
    "Answer in the same language as the user's query."
)

# Maximum character length for individual text fields (summary, title, snippet).
# Prevents single-result fields from dominating the output and hitting the
# 100K-character MCP automatic offload threshold.
_MAX_FIELD_LENGTH = 2000


class DeepSeekProvider:
    """Search provider backed by DeepSeek's OpenAI-compatible API with built-in web search."""

    def _api_url(self) -> str:
        base = os.environ.get("DEEPSEEK_BASE_URL") or _DEEPSEEK_DEFAULT_BASE_URL
        return base.rstrip("/") + "/v1/chat/completions"

    def _thinking_config(self) -> dict[str, str]:
        enabled = os.environ.get("ARCHON_WEB_DEEPSEEK_THINKING", "").lower() in ("true", "1", "yes")
        return {"type": "enabled"} if enabled else {"type": "disabled"}

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        # Note: max_results is accepted for protocol compatibility but has no
        # effect — DeepSeek's built-in web search is model-driven and the
        # number of returned sources is determined internally.
        _ = max_results
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return json.dumps({"query": query, "results": [], "result_count": 0, "error": "DEEPSEEK_API_KEY not set"}, ensure_ascii=False)

        model = kwargs.get("model", "deepseek-v4-flash")

        body: dict = {
            "model": model,
            "max_tokens": 8192,
            "messages": [
                {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "tools": [{"type": "web_search_20250305", "function": {"name": "web_search"}}],
            "tool_choice": "auto",
        }
        # Default: thinking disabled (saves tokens). Enable via ARCHON_WEB_DEEPSEEK_THINKING=true
        if self._thinking_config().get("type") == "enabled":
            body["thinking"] = {"type": "enabled"}

        try:
            timeout = int(os.environ.get("ARCHON_WEB_TIMEOUT", "30"))
        except ValueError:
            print("Warning: invalid ARCHON_WEB_TIMEOUT value, falling back to 30s", file=sys.stderr)
            timeout = 30
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    self._api_url(),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                return self._format(query, data)

        except httpx.HTTPStatusError as e:
            print(f"Error: DeepSeek HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: DeepSeek request failed: {e}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: DeepSeek search failed: {e}", file=sys.stderr)
            return json.dumps({"query": query, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

    def _format(self, query: str, data: dict) -> str:
        """Extract search results and text answer from OpenAI-format response.

        Parses choices[0].message.content for the text answer and
        choices[0].message.tool_calls or annotations for search sources.

        Long text fields are truncated to _MAX_FIELD_LENGTH to prevent excessive
        output size and avoid the MCP 100K-character automatic offload threshold.
        """
        text_answer = ""
        sources = []

        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = (message.get("content") or "").strip()
            if content:
                text_answer = content[:_MAX_FIELD_LENGTH] + (
                    "..." if len(content) > _MAX_FIELD_LENGTH else ""
                )

            # Extract sources from tool calls (function calling style)
            for tool_call in message.get("tool_calls") or []:
                if tool_call.get("function", {}).get("name") == "web_search":
                    try:
                        args = json.loads(tool_call["function"].get("args", "{}"))
                        sources.append({
                            "title": args.get("query", query)[:_MAX_FIELD_LENGTH],
                            "url": "",
                        })
                    except (json.JSONDecodeError, KeyError):
                        pass

            # Extract sources from annotations if present (DeepSeek extension)
            for annotation in message.get("annotations") or []:
                if annotation.get("type") == "web_search_result":
                    sources.append({
                        "title": (annotation.get("title") or "")[:_MAX_FIELD_LENGTH],
                        "url": annotation.get("url", ""),
                    })

        result = {
            "query": query,
            "results": sources,
            "result_count": len(sources),
        }
        if text_answer:
            result["summary"] = text_answer

        return json.dumps(result, ensure_ascii=False)
