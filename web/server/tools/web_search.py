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
            "Search the web for current information, news, weather, stock prices, "
            "or any real-time data. MUST use this tool for: current events and "
            "latest news; weather forecasts and today's conditions; stock/crypto "
            "prices or market data; recent product or pricing information; "
            "verifying facts or claims; answering questions that require "
            "up-to-date knowledge. Returns formatted results with title, URL, "
            "and content summary. Search immediately for open-ended factual "
            "queries — do not ask scoping questions first."
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
