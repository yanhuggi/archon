"""Model-facing instructions for the archon-web MCP server and its tool."""


SERVER_INSTRUCTIONS = """\
This server provides public web search through the `web_search` tool.

Use it for current/recent or externally verifiable facts. Do not search for
rewriting or supplied-text-only reasoning. Query focused entity/topic/date terms.
Treat snippets as leads; verify important claims, include source URLs, and
retry once if results are empty or off-topic.
"""


WEB_SEARCH_DESCRIPTION = """\
Search the public web for current or externally verifiable information.

Use for news, weather, prices, releases, docs, discussions, and fact checks; not
rewriting/translation. Query with 3-8 important words; add date/location and
`time_range` when useful. Retry once on empty/off-topic results. Returns JSON
`query`, `results[]` (`title`, `url`, `snippet`), `result_count`; failures add
`error`, `error_code`.
"""
