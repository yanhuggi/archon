# archon-web

基于 Tavily API 的轻量级 MCP 网页搜索服务。服务只暴露 `web_search` 工具，结果以 JSON 文本返回，适用于 Claude Code、Codex 和其他 MCP 客户端。

## 配置

必须设置 Tavily API Key：

```bash
export TAVILY_API_KEY="tvly-..."
```

也可以放入 `web/.env` 或当前目录的 `.env`。可选变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TAVILY_SEARCH_DEPTH` | `basic` | Tavily 搜索深度，可设为 `advanced` |
| `TAVILY_TOPIC` | `general` | 搜索主题，可设为 `news` 或 `finance` |
| `ARCHON_WEB_TIMEOUT` | `10` | 上游请求超时（1–120 秒） |
| `ARCHON_WEB_TRANSPORT` | `stdio` | `stdio`、`sse` 或 `streamable-http` |
| `ARCHON_WEB_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `ARCHON_WEB_PORT` | `8000` | HTTP 监听端口 |

启动：

```bash
uv run archon-web
```

调用示例：

```text
web_search(query="MCP Python SDK 2.0", max_results=5, time_range="month")
```

`time_range` 可选 `day`、`week`、`month`、`year`。结果包含 `title`、`url`、`snippet`，以及 Tavily 的相关性 `score`（若提供）。

没有配置 API Key 时，工具返回 `error_code: "missing_api_key"`；上游限流、超时和网络错误分别返回 `rate_limited`、`upstream_timeout` 和 `upstream_error`。
