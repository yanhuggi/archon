# archon-web

MCP (Model Context Protocol) 服务器，为 AI 编程助手提供联网搜索能力。支持多搜索厂商切换。

## 功能

- **联网搜索** — 一个 `web_search` 工具，多个搜索后端
- **多厂商支持** — Tavily、DeepSeek、DuckDuckGo，按需切换
- **结构化响应** — 统一 JSON 格式 `{query, results[], result_count}`
- **按需启用** — 未配置 API Key 的厂商自动隐藏工具
- **可扩展** — 添加新搜索厂商只需一个文件 + 一行注册

## 搜索厂商

| 厂商 | 配置 | 获取 Key | 特点 |
|---|---|---|---|
| **tavily** | `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | 结构化搜索结果，有评分，免费 1000 次/月 |
| **deepseek** | `DEEPSEEK_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com) | 服务端原生搜索，返回 AI 总结答案（默认关闭 thinking，节省 token） |
| **duckduckgo** | `ARCHON_WEB_DUCKDUCKGO_ENABLED=true` | 无需 Key | 免费，无 Key 即可使用（需 opt-in，带频率限制） |

## 快速开始

### 前置条件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 从 Git 安装（推荐）
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=web

# 或本地开发
cd web
uv sync --extra duckduckgo
```

### 配置

复制环境变量模板并填入密钥：

```bash
cp .env.example .env
```

至少配置一个搜索源：
- **有 API Key** → 填入 `TAVILY_API_KEY` 或 `DEEPSEEK_API_KEY`
- **无 API Key** → 设置 `ARCHON_WEB_DUCKDUCKGO_ENABLED=true`

### 测试

```bash
# 默认厂商
bash test_mcp.sh "今天福州天气"

# 指定厂商
bash test_mcp.sh "今天福州天气" deepseek
bash test_mcp.sh "今天福州天气" duckduckgo
```

## 集成到 Claude Code

### Git 安装（推荐）

```bash
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=web
```

配置 Claude Code：

```bash
# 使用 Tavily
claude mcp add -s user archon-web \
  --env TAVILY_API_KEY=tvly-xxx \
  -- archon-web

# 仅 DuckDuckGo（无需 Key）
claude mcp add -s user archon-web \
  --env ARCHON_WEB_DUCKDUCKGO_ENABLED=true \
  -- archon-web

# 混用
claude mcp add -s user archon-web \
  --env TAVILY_API_KEY=tvly-xxx \
  --env ARCHON_WEB_DUCKDUCKGO_ENABLED=true \
  -- archon-web
```

### 手动配置

编辑 `~/.claude/mcp.json`：

```json
{
  "mcpServers": {
    "archon-web": {
      "command": "archon-web",
      "env": {
        "ARCHON_WEB_DUCKDUCKGO_ENABLED": "true"
      }
    }
  }
}
```

### 提示词配置

为了让模型主动调用 `web_search`（尤其是使用不支持内置搜索的后端模型时），在项目的 `CLAUDE.md` 或全局 `~/.claude/CLAUDE.md` 中添加：

```markdown
# Tool Usage Policy

## Web Search

Use `web_search` for any real-time or externally verifiable information.
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|---|
| `TAVILY_API_KEY` | — | Tavily API Key |
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key |
| `ARCHON_WEB_DUCKDUCKGO_ENABLED` | — | 设为 `true` 启用 DuckDuckGo |
| `ARCHON_WEB_DUCKDUCKGO_INTERVAL` | `2.0` | DuckDuckGo 请求间隔（秒），防限流 |
| `ARCHON_WEB_DEEPSEEK_THINKING` | `false` | 设为 `true` 启用 DeepSeek thinking（消耗额外 token） |
| `ARCHON_WEB_PROVIDER` | 自动 | 强制指定默认厂商 `tavily`/`deepseek`/`duckduckgo` |
| `TAVILY_API_URL` | 默认 API | 自定义 Tavily API 地址（完整 URL） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/anthropic` | DeepSeek BASE_URL，自动拼接 `/v1/messages` |

### 默认厂商选择逻辑

1. `ARCHON_WEB_PROVIDER` 显式指定 → 使用指定值
2. 有 `TAVILY_API_KEY` → `tavily`
3. 有 `DEEPSEEK_API_KEY` → `deepseek`
4. DuckDuckGo 已启用 → `duckduckgo`
5. 无任何配置 → 工具隐藏，模型不可见

### 模型调用时切换

```python
web_search(query="福州天气")                    # 走默认厂商
web_search(query="福州天气", provider="deepseek") # 显式指定
web_search(query="福州天气", provider="duckduckgo")
```

## 响应格式

统一返回 JSON 字符串：

```json
{
  "query": "福州天气",
  "results": [
    {
      "title": "福州天气预报",
      "url": "https://...",
      "snippet": "福州今天多云..."
    }
  ],
  "result_count": 5
}
```

各厂商额外字段：

| 厂商 | 额外字段 |
|---|---|
| tavily | `results[].score`（相关性评分） |
| deepseek | `summary`（AI 生成的总结答案） |

## 测试

```bash
uv run --directory web pytest web/tests -v
```

所有测试 mock 外部 API，无需真实 API Key。

## 项目结构

```
web/
├── pyproject.toml               # 项目配置
├── .env                         # API Key（gitignored）
├── .env.example                 # 环境变量模板
├── .gitignore
├── README.md
├── uv.lock
├── test_mcp.sh                  # 快速集成测试
├── server/
│   ├── main.py                  # MCP 入口
│   ├── providers/
│   │   ├── __init__.py          # Provider 注册表
│   │   ├── tavily.py
│   │   ├── deepseek.py
│   │   └── duckduckgo.py
│   └── tools/
│       └── web_search.py
└── tests/
    ├── conftest.py              # 共享 fixture
    ├── test_providers_init.py   # 注册表测试（12 项）
    ├── test_tavily.py           # Tavily 测试（14 项）
    ├── test_deepseek.py         # DeepSeek 测试（24 项）
    ├── test_duckduckgo.py       # DuckDuckGo 测试（12 项）
    └── test_web_search.py       # 工具测试（10 项）
```

## 扩展：添加新的搜索厂商

两步完成：

1. 创建 `server/providers/xxx.py`：

```python
class XxxProvider:
    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        # 返回 JSON 字符串
        return json.dumps({"query": query, "results": [...], "result_count": n})
```

2. 在 `server/main.py` 注册：

```python
from server.providers.xxx import XxxProvider
register_provider("xxx", XxxProvider())
```

## 架构说明

- **工具层**（`tools/`）：定义 MCP 工具，对外统一接口
- **提供者层**（`providers/`）：封装不同搜索后端的 API 调用，通过 `SearchProvider` 协议统一
- **注册表**（`providers/__init__.py`）：管理厂商注册与查找，厂商不可用时自动隐藏工具
