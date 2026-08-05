# archon-web

`archon-web` 是一个轻量级 MCP（Model Context Protocol）联网搜索服务。它通过 `ddgs` 的公开搜索后端提供网页搜索，无需 API Key，适合为 Claude Code、Codex 和其他 MCP 客户端补充实时信息检索能力。

## 能力概览

| MCP 能力 | 名称 | 说明 |
|---|---|---|
| Tool | `web_search` | 搜索公开网页，返回标题、URL 和摘要 |
| Transport | `stdio` | 本地 MCP 客户端默认接入方式 |
| Transport | `streamable-http` | 可选 HTTP 服务，默认端点 `/mcp` |
| Transport | `sse` | 兼容旧客户端的 SSE 方式 |

主要特性：

- 无需 API Key，安装后即可使用。
- MCP server 内置使用边界、查询改写、来源核验和引用指引。
- 工具参数包含长度/范围约束，并提供可选时间过滤；参数校验失败同样返回 JSON 包络。
- 中文查询自动使用中文区域结果，日文查询（含半角片假名）不会被误判为中文。
- 成功与失败均返回稳定 JSON 包络，便于模型和程序消费。
- 内置同一用户下的跨进程请求间隔，多个自动启动的 stdio 服务共享限流状态。
- 日志写入 `stderr`，不会污染 stdio MCP 的 JSON-RPC 通道。

> 公开搜索上游不提供可用性 SLA。搜索摘要适合发现和初步核验信息，不等同于完整网页内容。

## 快速开始

### 前置条件

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

### 从 Git 安装

```bash
# 稳定分支
uv tool install --force \
  git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=web
```

安装完成后确认命令可用：

```bash
archon-web --help
```

### 本地开发

```bash
git clone git@github.com:yanhuggi/archon.git
cd archon/web
uv sync --group dev
uv run archon-web --help
```

## 接入 MCP 客户端

### Claude Code

如果已通过 `uv tool install` 安装：

```bash
claude mcp add -s user archon-web -- archon-web
```

直接从本地源码运行：

```bash
claude mcp add -s project archon-web -- \
  uv run --directory /absolute/path/to/archon/web archon-web
```

检查连接状态：

```bash
claude mcp list
```

### 通用 JSON 配置

```json
{
  "mcpServers": {
    "archon-web": {
      "command": "archon-web"
    }
  }
}
```

如果命令没有安装到客户端可见的 `PATH`，请把 `command` 改为 `archon-web` 的绝对路径。

## `web_search` 工具

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 聚焦的搜索词，1–500 字符 |
| `max_results` | integer | `8` | 返回数量，范围 1–20 |
| `time_range` | string/null | `null` | 可选：`day`、`week`、`month`、`year`，大小写不敏感 |

调用示例：

```text
web_search(query="MCP Python SDK 2.0 migration", max_results=8)
web_search(query="福州天气 2026-07-30", max_results=5, time_range="day")
web_search(query="site:docs.python.org asyncio TaskGroup", max_results=5)
```

查询建议：

- 优先使用 3–8 个关键字，保留实体、主题、地点和日期。
- “最新”“今天”等问题应加入明确日期，或设置 `time_range`。
- 首次结果为空或偏题时，换一组实质不同的关键词重试一次。
- 摘要只是线索；重要结论应比较多个来源，并在回答中保留 URL。

### 成功响应

工具返回 JSON 字符串：

```json
{
  "query": "MCP Python SDK 2.0 migration",
  "results": [
    {
      "title": "MCP Python SDK documentation",
      "url": "https://example.com/docs",
      "snippet": "Migration notes and examples..."
    }
  ],
  "result_count": 1
}
```

### 失败响应

失败仍使用同一包络，`results` 为空：

```json
{
  "query": "example",
  "results": [],
  "result_count": 0,
  "error": "Web search provider failed: ...",
  "error_code": "upstream_error"
}
```

常见 `error_code`：

| 代码 | 含义 |
|---|---|
| `invalid_query` | 查询为空或超过长度限制 |
| `invalid_max_results` | 返回数量不是有效整数 |
| `invalid_time_range` | `time_range` 不在 `day`/`week`/`month`/`year` 之内 |
| `provider_unavailable` | 搜索 provider 未注册 |
| `invalid_provider_response` | 上游返回内容不符合 JSON 契约 |
| `rate_limited` | 被搜索上游限流，可稍后重试 |
| `upstream_timeout` | 上游在 `ARCHON_WEB_TIMEOUT` 内未返回 |
| `upstream_error` | 其他搜索上游网络或代理错误 |

参数的类型、长度和取值范围都会在 JSON Schema 中声明供客户端参考，但一律由工具内部校验并返回上面的包络，因此各类失败共享同一组字段，不会出现 MCP 层的通用报错。传入错误类型（例如 `max_results` 传字符串或布尔值）同样返回包络，不会被静默转换后继续搜索。

## 模型使用策略

服务通过 MCP `instructions` 和 `web_search` 工具描述自动提供使用边界、查询建议和来源核验要求，不额外暴露研究工作流 Prompt。是否搜索、如何改写查询、是否进行语义重试以及如何组织引用，由模型根据当前任务决定。

若希望在 Claude Code 的 Tool Search 场景中提高调用稳定性，可在项目的 `CLAUDE.md`、`AGENTS.md` 或等效系统提示词中加入以下策略。它不仅限定搜索工具，还用可观察条件明确何时必须在回答前搜索：

```markdown
## Web Search

`web_search` is the only permitted web search tool. Do not use built-in web
search, shell commands, browser automation, or other mechanisms as substitutes.

Before answering, you MUST call `web_search` at least once when any of these
conditions applies:

- The user asks about current, latest, recent, today, or time-sensitive information.
- The answer depends on software versions, release notes, current documentation,
  API behavior, compatibility, deprecations, pricing, availability, or open issues.
- The user asks to verify a claim or requests sources, citations, or links.
- A factual claim depends on information outside the conversation and could have
  changed since the model's knowledge cutoff.
- You are uncertain whether remembered external information is accurate or current.

For official documentation, include the product or project name and prefer a
domain-qualified query such as `site:docs.example.com`.

After searching:

- Treat result snippets as leads, not conclusive evidence.
- Compare multiple results for important or disputed claims.
- Include the relevant result URLs in the answer.
- If results are empty or irrelevant, retry once with materially different keywords.
- If evidence remains insufficient, state that clearly instead of guessing.

Do not search when the task only involves:

- Rewriting, translating, summarizing, or transforming user-provided content.
- Reasoning entirely from files or information already available in the conversation.
- Stable facts that do not require current or external verification.

If `web_search` is unavailable, report that limitation. Do not silently replace
it with another search mechanism or present remembered information as verified.
```

`All web searches must use web_search` 只约束工具路由，并不能稳定触发搜索；上面的 `Before answering, you MUST call` 与具体触发条件共同定义了调用门槛。模型执行提示词仍具有概率性，且可能受更高优先级指令影响，因此无法保证每次都调用，但这种写法更利于 Tool Search 匹配并显著减少模型仅凭记忆回答的情况。

## 运行方式

### stdio（默认）

```bash
archon-web
```

这是 Claude Code 等本地 MCP 客户端的推荐方式。不要把普通日志写入 stdout；本服务已将日志保留在 stderr。

客户端会按需自动启动和停止进程，无需手动维护常驻服务。多个本地客户端进程通过用户缓存目录中的时间戳文件共享搜索请求间隔；该文件只保存最近一次请求时间，不包含查询内容。

### Streamable HTTP

```bash
archon-web --transport streamable-http --host 127.0.0.1 --port 8000
```

端点默认为：

```text
http://127.0.0.1:8000/mcp
```

也可全部通过环境变量配置。服务未内置身份认证，因此除非外层已有可信反向代理和鉴权，不要直接绑定公网地址。

### SSE（兼容模式）

```bash
archon-web --transport sse --host 127.0.0.1 --port 8000
```

新接入优先选择 stdio 或 Streamable HTTP。

## 配置

复制模板：

```bash
cp .env.example .env
```

查找顺序为 `web/.env`、当前工作目录 `.env`、`~/.config/archon-web/.env`，命中第一个后停止。MCP 客户端或 Shell 显式传入的环境变量优先于 `.env`。

### 搜索配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `ARCHON_WEB_DUCKDUCKGO_INTERVAL` | `2.0` | 同一用户下请求开始时间的最小间隔，范围 0–60 秒（保留旧变量名以兼容现有配置） |
| `ARCHON_WEB_TIMEOUT` | `10` | 上游请求超时，范围 1–120 秒 |
| `ARCHON_WEB_PROXY` | 空 | HTTP/HTTPS/SOCKS5 代理 URL |
| `ARCHON_WEB_RATE_LIMIT_FILE` | `~/.cache/archon-web/duckduckgo-rate-limit` | 跨进程限流状态文件，建议使用绝对路径 |

### MCP 服务配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `ARCHON_WEB_TRANSPORT` | `stdio` | `stdio`、`streamable-http` 或 `sse` |
| `ARCHON_WEB_HOST` | `127.0.0.1` | HTTP/SSE 监听地址 |
| `ARCHON_WEB_PORT` | `8000` | HTTP/SSE 端口 |
| `ARCHON_WEB_LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `ARCHON_WEB_STREAMABLE_HTTP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `ARCHON_WEB_STATELESS_HTTP` | `false` | 是否使用无状态 HTTP 模式 |
| `ARCHON_WEB_SSE_PATH` | `/sse` | SSE 连接路径 |
| `ARCHON_WEB_MESSAGE_PATH` | `/messages/` | SSE 消息路径 |

命令行中的 `--transport`、`--host`、`--port` 会覆盖对应环境变量。

## 测试

单元测试全部 mock 外部搜索，无需真实网络：

```bash
cd web
uv sync --group dev
uv run pytest tests -q
```

执行真实 stdio MCP + 公开搜索上游冒烟测试：

```bash
cd web
bash test_mcp.sh "MCP Python SDK 2.0" 5 week
```

该脚本使用 MCP `2026-07-28` 的 `server/discover` 完成能力协商并调用 `web_search`。它需要能够访问所选公开搜索上游。

## 常见问题

### 返回 `rate_limited`、`upstream_timeout` 或 `upstream_error`

- `rate_limited` 表示被上游限流：增大 `ARCHON_WEB_DUCKDUCKGO_INTERVAL`，例如设为 `3` 或 `5`。
- `upstream_timeout` 表示上游未在期限内返回：增大 `ARCHON_WEB_TIMEOUT`，或检查代理链路。
- 检查当前网络是否能访问公开搜索上游。
- 如需代理，设置 `ARCHON_WEB_PROXY`。
- 同一用户的多个本地进程会自动共享限流；不同主机、容器或用户仍各自计数。
- 检查 `ARCHON_WEB_RATE_LIMIT_FILE` 的父目录是否可写；不可写时服务会降级为进程内限流。

### 搜索结果为空或不相关

- 缩短自然语言问题，改为实体 + 主题 + 日期/地点。
- 对最新内容设置 `time_range`。
- 用同义词或官方域名限定（例如 `site:docs.python.org`）重试一次。

### MCP 客户端找不到 `archon-web`

- 运行 `command -v archon-web` 检查安装路径。
- 在 MCP JSON 配置中使用绝对路径。
- 本地源码模式确认 `uv run --directory /absolute/path/to/archon/web archon-web --help` 可执行。

## 项目结构

```text
web/
├── .env.example
├── README.md
├── pyproject.toml
├── test_mcp.sh
├── uv.lock
├── server/
│   ├── config.py                 # 环境变量解析与边界校验
│   ├── main.py                   # MCP server、传输和 CLI 入口
│   ├── instructions.py           # server instructions 与工具使用说明
│   ├── providers/
│   │   ├── __init__.py           # provider 注册表与协议
│   │   └── duckduckgo.py         # 搜索、限流、超时和结果清洗
│   └── tools/
│       └── web_search.py         # MCP 工具定义与稳定响应契约
└── tests/
    ├── test_config.py
    ├── test_duckduckgo.py
    ├── test_main.py
    ├── test_instructions.py
    ├── test_providers_init.py
    └── test_web_search.py
```
