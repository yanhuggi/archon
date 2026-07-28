#!/usr/bin/env bash
# Quick integration test: send MCP request to archon-web server
# Usage: TAVILY_API_KEY=tvly-xxx bash test_mcp.sh "搜索关键词" [provider]
#   provider: tavily (default) | deepseek

set -euo pipefail

QUERY="${1:-"2026年AI发展趋势"}"
PROVIDER="${2:-tavily}"

# Build JSON-RPC messages using Python to avoid shell injection
PAYLOAD=$(
  python3 -c "
import json, sys
init = {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0.0'}}}
notify = {'jsonrpc':'2.0','method':'notifications/initialized'}
call = {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'web_search','arguments':{'query':sys.argv[1],'provider':sys.argv[2]}}}
print(json.dumps(init))
print(json.dumps(notify))
print(json.dumps(call))
" "$QUERY" "$PROVIDER"
)

echo "$PAYLOAD" |
  uv run --directory "$(dirname "$0")" archon-web 2>/dev/null |
  python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get('id') == 2 and 'result' in msg:
        content = msg['result']['content'][0]['text']
        print('=== 搜索结果 ===')
        print(content)
        break
"
