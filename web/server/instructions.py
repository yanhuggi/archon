"""Model-facing instructions for the archon-web MCP server and its tool."""


SERVER_INSTRUCTIONS = """\
This server provides public web search through the `web_search` tool.

Use `web_search` when an answer depends on current events, live prices or
weather, a recent release, external documentation, or a fact that should be
verified against an external source. Do not search for tasks that only require
transforming text or reasoning over information already supplied by the user.

Search with a focused query containing the important entity, action, and time
period. For a time-sensitive question, include an explicit date or use the
tool's `time_range` argument. Treat snippets as leads rather than proof, check
multiple results when the claim matters, and include the source URLs in the
final answer. If the first search is empty or off-topic, retry once with
different wording before reporting that no useful result was found.
"""


WEB_SEARCH_DESCRIPTION = """\
Search the public web for current or externally verifiable information.

Use this tool for current events, recent news, weather, prices, releases,
library/API documentation, issue discussions, and fact checking. Do not use it
for rewriting, translation, or questions that can be answered from the user's
provided text alone.

Query guidance:
- Write one focused query with roughly 3-8 important words (entity + topic +
  location or date where useful).
- For latest/current questions, include a concrete date such as 2026-07-30;
  use `time_range` when a recency filter is helpful.
- If results are empty or irrelevant, retry once with materially different
  keywords. Do not ask a clarification question before trying an obvious
  search.

Arguments:
- `query`: search terms, 1-500 characters.
- `max_results`: number of results to return, 1-20 (default 8).
- `time_range`: optional freshness filter: `day`, `week`, `month`, or `year`.

The result is JSON with `query`, `results[]` (`title`, `url`, `snippet`), and
`result_count`. A failed search includes an `error` string and an `error_code`;
never present an error response as verified evidence.
"""
