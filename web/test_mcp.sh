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

PAYLOAD=$(
  python3 -c '
import json
import sys

arguments = {"query": sys.argv[1], "max_results": int(sys.argv[2])}
if sys.argv[3]:
    arguments["time_range"] = sys.argv[3]

protocol_version = "2026-07-28"
discover_meta = {
    "io.modelcontextprotocol/protocolVersion": protocol_version,
    "io.modelcontextprotocol/clientInfo": {
        "name": "archon-web-smoke-test",
        "version": "1.0.0",
    },
    "io.modelcontextprotocol/clientCapabilities": {},
}
request_meta = {"io.modelcontextprotocol/protocolVersion": protocol_version}

messages = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {"_meta": discover_meta},
    },
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "web_search",
            "arguments": arguments,
            "_meta": request_meta,
        },
    },
]
for message in messages:
    print(json.dumps(message, ensure_ascii=False))
' "$QUERY" "$MAX_RESULTS" "$TIME_RANGE"
)

echo "$PAYLOAD" |
  uv run --directory "$WEB_DIR" archon-web |
  python3 -c '
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue
    if message.get("id") != 2:
        continue
    if "error" in message:
        print(json.dumps(message["error"], ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    result = message.get("result", {})
    content = result.get("content", [])
    if not content:
        print("MCP 响应中没有工具结果", file=sys.stderr)
        raise SystemExit(1)
    text = content[0].get("text", "")
    print("=== 搜索结果 ===")
    print(text)
    if result.get("isError"):
        raise SystemExit(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit(0)
    if payload.get("error"):
        raise SystemExit(1)
    raise SystemExit(0)

print("未收到 web_search 响应", file=sys.stderr)
raise SystemExit(1)
'
