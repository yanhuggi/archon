# archon-vision

MCP (Model Context Protocol) 服务器，为 AI 编程助手提供图片分析能力。支持多厂商切换，当前支持小米 MiMo 视觉模型。

## 功能

- **图片分析** — 一个 `analyze_image` 工具，支持 URL、Base64 和本地文件路径
- **多厂商架构** — 可扩展，当前支持小米 MiMo v2.5
- **结构化响应** — 统一 JSON 格式 `{image_url, prompt, understanding, model}`
- **按需启用** — 未配置 API Key 时自动隐藏工具
- **可扩展** — 添加新厂商只需一个文件 + 一行注册

## 视觉厂商

| 厂商 | 配置 | 获取 Key |
|---|---|---|
| **mimo** | `MIMO_API_KEY` | [mimo.mi.com](https://mimo.mi.com) |

## 快速开始

### 前置条件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 从 Git 安装（推荐）
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=vision

# 或本地开发
cd vision
uv sync
```

### 配置

复制环境变量模板并填入密钥：

```bash
cp .env.example .env
```

至少配置一个图片源：

```bash
# 编辑 .env
MIMO_API_KEY=your-mimo-api-key
```

### 测试

```bash
cd vision
uv run pytest -v
```

## 集成到 Claude Code

### Git 安装（推荐）

```bash
uv tool install --force git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=vision
```

配置 Claude Code：

```bash
claude mcp add -s user archon-vision \
  --env MIMO_API_KEY=your-mimo-api-key \
  -- archon-vision
```

### 手动配置

编辑 `~/.claude/mcp.json`：

```json
{
  "mcpServers": {
    "archon-vision": {
      "command": "archon-vision",
      "env": {
        "MIMO_API_KEY": "your-mimo-api-key"
      }
    }
  }
}
```

### 提示词配置

为了让模型主动调用 `analyze_image`（尤其是使用不支持内置视觉的后端模型时），在项目的 `CLAUDE.md` 或全局 `~/.claude/CLAUDE.md` 中添加：

```markdown
# Tool Usage Policy

## Image Analysis

Use `analyze_image` when the user provides an image (via URL, local file, or drag-and-drop) or asks about the content of an image.
```

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MIMO_API_KEY` | — | 小米 MiMo API Key |
| `MIMO_BASE_URL` | `https://api.xiaomimimo.com/v1` | BASE_URL，代码自动拼接 `/chat/completions` |
| `MIMO_MODEL` | `mimo-v2.5` | 模型名称 |

## 响应格式

统一返回 JSON 字符串：

```json
{
  "image_url": "https://example.com/photo.jpg",
  "prompt": "请详细描述这张图片的内容",
  "understanding": "这张图片展示了一片美丽的日落...",
  "model": "mimo-v2.5"
}
```

各厂商额外字段：

| 厂商 | 额外字段 |
|---|---|
| mimo | `reasoning_content`（深度思考过程，如启用深度思考模式） |

## 项目结构

```
vision/
├── pyproject.toml               # 项目配置
├── .env                         # API Key（gitignored）
├── .env.example                 # 环境变量模板
├── .gitignore
├── README.md
├── server/
│   ├── main.py                  # MCP 入口
│   ├── providers/
│   │   ├── __init__.py          # Provider 注册表
│   │   └── mimo.py
│   └── tools/
│       └── analyze_image.py
└── tests/
    ├── conftest.py              # 共享 fixture
    ├── test_mimo.py             # MiMo 测试
    └── test_analyze_image.py    # 工具测试
```

## 扩展：添加新的视觉厂商

两步完成：

1. 创建 `server/providers/xxx.py`：

```python
class XxxProvider:
    def understand(self, image_url: str, prompt: str = "描述图片", **kwargs) -> str:
        # 返回 JSON 字符串
        return json.dumps({
            "image_url": image_url,
            "prompt": prompt,
            "understanding": "...",
            "model": "xxx-model",
        })
```

2. 在 `server/main.py` 注册：

```python
from server.providers.xxx import XxxProvider
register_provider("xxx", XxxProvider())
```

## 架构说明

- **工具层**（`tools/`）：定义 MCP 工具，对外统一接口
- **提供者层**（`providers/`）：封装不同视觉后端的 API 调用，通过 `ImageProvider` 协议统一
- **注册表**（`providers/__init__.py`）：管理厂商注册与查找，厂商不可用时自动隐藏工具
