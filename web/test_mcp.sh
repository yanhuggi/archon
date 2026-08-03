#!/usr/bin/env bash
# End-to-end stdio test for the archon-web MCP server.
# Usage: bash test_mcp.sh [query] [max_results] [day|week|month|year]

set -euo pipefail

QUERY="${1:-2026年AI发展趋势}"
MAX_RESULTS="${2:-8}"
TIME_RANGE="${3:-}"
WEB_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! [[ "$MAX_RESULTS" =~ ^[0-9]+$ ]]; then
  echo "max_results 必须是整数" >&2
  exit 2
fi

uv run --directory "$WEB_DIR" python -c '
import anyio
import json
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client


async def main() -> None:
    query, max_results, time_range, web_dir = sys.argv[1:]
    arguments = {"query": query, "max_results": int(max_results)}
    if time_range:
        arguments["time_range"] = time_range

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.main"],
        cwd=web_dir,
    )
    async with stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            await session.discover()
            result = await session.call_tool("web_search", arguments)

    content = result.content
    if not content:
        print("MCP 响应中没有工具结果", file=sys.stderr)
        raise SystemExit(1)
    text = getattr(content[0], "text", "")
    print("=== 搜索结果 ===")
    print(text)
    if result.is_error:
        raise SystemExit(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit(0)
    if payload.get("error"):
        raise SystemExit(1)


anyio.run(main)
' "$QUERY" "$MAX_RESULTS" "$TIME_RANGE" "$WEB_DIR"
