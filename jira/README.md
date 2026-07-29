# archon-jira

MCP (Model Context Protocol) 服务器，为 AI 编程助手提供 Jira 任务查询能力。

## 功能

- **JQL 搜索** — `search_issues` 工具，支持任意 JQL 查询
- **任务详情** — `get_issue` 工具，包含关联关系（带类型）、子任务、备注、附件列表
- **附件下载** — `get_attachment` 工具，下载附件内容（文本直返 / 图片 base64）
- **按需启用** — 未配置连接信息时工具自动隐藏
- **可扩展** — Provider Protocol 模式，可添加 Jira Cloud 等其他后端

## 工具一览

| 工具 | 用途 | 返回内容 |
|---|---|---|
| `search_issues` | JQL 搜索任务 | key、summary、status、assignee、类型、优先级、标签 |
| `get_issue` | 获取任务详情 | 完整字段 + issue links（带关系类型）+ sub-tasks + parent + attachments |
| `get_attachment` | 下载附件 | 流式下载到指定路径，自动创建父目录 |

## 快速开始

### 前置条件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) 包管理器
- Jira Server 实例（支持 REST API v2）

### 安装

```bash
# 从 Git 安装（推荐）
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=jira

# 或本地开发
cd jira
uv sync
```

### 配置

复制环境变量模板并填入连接信息：

```bash
cp .env.example .env
```

```
JIRA_URL=https://jira.example.com
JIRA_USERNAME=your-username
JIRA_PASSWORD=your-password
```

### 测试

```bash
uv run pytest -v
```

所有测试 mock 外部 API，无需真实 Jira 连接。

## 集成到 Claude Code

### Git 安装（推荐）

```bash
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=jira
```

配置 Claude Code：

```bash
claude mcp add -s user archon-jira \
  --env JIRA_URL=https://jira.example.com \
  --env JIRA_USERNAME=your-username \
  --env JIRA_PASSWORD=your-password \
  -- archon-jira
```

### 手动配置

编辑 `~/.claude/mcp.json`：

```json
{
  "mcpServers": {
    "archon-jira": {
      "command": "archon-jira",
      "env": {
        "JIRA_URL": "https://jira.example.com",
        "JIRA_USERNAME": "your-username",
        "JIRA_PASSWORD": "your-password"
      }
    }
  }
}
```

### 推荐方式

| 方式 | 配置位置 | 说明 |
|---|---|---|
| `claude mcp add --env` | `~/.claude.json` | **推荐**，配置随 Claude Code 统一管理 |
| `~/.claude/mcp.json` | `~/.claude/mcp.json` | 手动编辑，适合批量管理多个 MCP |

### .env 文件回退

服务器启动时会按以下顺序查找 `.env` 文件（找到即停）：

1. 项目目录 `jira/.env`
2. 当前工作目录 `.env`
3. `~/.config/archon-jira/.env`

如果通过 `claude mcp add --env` 或 `mcp.json` 配置了环境变量，则无需 `.env` 文件。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JIRA_URL` | — | Jira Server 基础 URL（必须） |
| `JIRA_USERNAME` | — | Jira 用户名（必须） |
| `JIRA_PASSWORD` | — | Jira 密码（必须） |
| `JIRA_TIMEOUT` | `30` | HTTP 超时时间（秒） |
| `JIRA_MAX_ATTACHMENT_SIZE` | `10485760` | 附件下载大小上限（字节，默认 10MB） |

## 响应格式

### search_issues

```json
{
  "jql": "project = PROJ AND status = Open",
  "total": 120,
  "start_at": 0,
  "max_results": 50,
  "results": [
    {
      "key": "PROJ-123",
      "summary": "修复登录问题",
      "status": "In Progress",
      "assignee": "张三",
      "issue_type": "Bug",
      "priority": "High",
      "labels": ["urgent"],
      "created": "2025-01-15T10:30:00.000+0800",
      "updated": "2025-01-20T14:00:00.000+0800"
    }
  ],
  "result_count": 50
}
```

### get_issue

```json
{
  "key": "PROJ-123",
  "summary": "修复登录问题",
  "description": "用户无法登录...",
  "status": "In Progress",
  "issue_links": [
    {
      "type": "Blocks",
      "direction": "outward",
      "linked_issue": {"key": "PROJ-130", "summary": "部署", "status": "Open"}
    }
  ],
  "subtasks": [
    {"key": "PROJ-124", "summary": "后端修复", "status": "Done"}
  ],
  "parent": {"key": "PROJ-100", "summary": "登录重构"},
  "attachments": [
    {"id": "10001", "filename": "screenshot.png", "size": 204800, "mime_type": "image/png"}
  ]
}
```

### get_attachment

```json
{
  "id": "241152",
  "filename": "微信图片.png",
  "saved_to": "/tmp/jira_screenshot.png",
  "size": 497000,
  "mime_type": "image/png"
}
```

## 关联关系说明

`get_issue` 返回的 `issue_links` 已归一化，每条关系包含：

| 字段 | 说明 |
|---|---|
| `type` | 关系类型名称（如 "Blocks"、"is blocked by"、"Clones"） |
| `direction` | `"outward"`（当前任务是主动方）或 `"inward"`（当前任务是被动方） |
| `linked_issue` | 关联任务的 key、summary、status |

## 项目结构

```
jira/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── uv.lock
├── server/
│   ├── __init__.py
│   ├── main.py                  # MCP 入口
│   ├── providers/
│   │   ├── __init__.py          # JiraProvider Protocol + 注册表
│   │   └── jira.py              # Jira REST API 客户端
│   └── tools/
│       ├── __init__.py
│       ├── search_issues.py     # JQL 搜索工具
│       ├── get_issue.py         # 任务详情工具
│       └── get_attachment.py    # 附件下载工具
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_main.py
    ├── test_jira.py
    ├── test_search_issues.py
    ├── test_get_issue.py
    └── test_get_attachment.py
```

## 测试

```bash
uv run pytest -v
```

36 项测试全部 mock 外部 API，无需真实 Jira 连接。
