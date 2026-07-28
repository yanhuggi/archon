"""web_search tool definition."""

import json

from mcp.server.fastmcp import FastMCP

from server.providers import get_provider


def register(mcp: FastMCP, default_provider: str = "tavily") -> None:
    """Register the web_search tool on the given MCP server.

    Args:
        mcp: The FastMCP instance.
        default_provider: Default search provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Call this when the user asks about current events, recent information, "
            "real-time data, or anything that requires up-to-date knowledge beyond "
            "the model's training cutoff. Searches the web and returns formatted "
            "results with title, URL, and content summary. For open-ended factual "
            "queries, search immediately rather than asking scoping questions. "
            "Do NOT attempt to answer time-sensitive questions from prior knowledge."
        )
    )
    def web_search(
        query: str,
        provider: str = default_provider,
        max_results: int = 10,
    ) -> str:
        """Search the web and return formatted results.

        Args:
            query: The search query in natural language (e.g. "today's weather in Tokyo").
            provider: Search backend to use. One of "tavily", "deepseek", "duckduckgo".
            max_results: Maximum number of search results to return (1-20, default 10).

        Returns:
            JSON string with query, results list (title, url, snippet), and result_count.
        """
        # Clamp max_results to valid range
        max_results = min(max(max_results, 1), 20)

        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        return p.search(query, max_results=max_results)
