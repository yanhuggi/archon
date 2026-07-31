# archon-vision

`archon-vision` 是一个轻量级 MCP（Model Context Protocol）图片理解服务。它通过小米 MiMo 视觉模型分析照片、截图、图表、图示、UI 和扫描文档，适合为 Claude Code、Codex 及其他 MCP 客户端补充视觉能力。

## 能力概览

| MCP 能力 | 名称 | 说明 |
|---|---|---|
| Tool | `analyze_image` | 针对一张图片回答聚焦的视觉问题 |
| Transport | `stdio` | 本地 MCP 客户端默认接入方式 |
| Transport | `streamable-http` | 可选 HTTP 服务，默认端点 `/mcp` |
| Transport | `sse` | 兼容旧客户端的 SSE 方式 |

主要特性：

- 支持 HTTP/HTTPS URL、JPEG/PNG Base64 data URI 和授权目录内的本地 JPEG/PNG。
- MCP server instructions 和工具描述包含调用边界、提示词写法及不确定性要求。
- `analyze_image` 始终可发现；未配置 API Key 时返回稳定的配置错误。
- 本地路径、文件类型、文件签名和大小在上传前校验。
- 完整 Base64 不会回显到结果中，避免无意义地占用模型上下文。
- 日志写入 `stderr`，不会污染 stdio MCP 的 JSON-RPC 通道。

> 本地图片内容会发送给第三方小米 MiMo API。不要提交未获授权或包含无关敏感信息的文件。模型输出也可能存在 OCR、数值和细节识别错误，不应被无条件视为事实。

## 快速开始

### 前置条件

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- 小米 MiMo API Key

### 从 Git 安装

```bash
# 稳定分支
uv tool install --force \
  git+ssh://git@github.com/yanhuggi/archon.git@main#subdirectory=vision

# 验证 dev 分支中的最新改动
uv tool install --force \
  git+ssh://git@github.com/yanhuggi/archon.git@dev#subdirectory=vision
```

确认命令可用：

```bash
archon-vision --help
```

### 本地开发

```bash
git clone git@github.com:yanhuggi/archon.git
cd archon/vision
uv sync --group dev
uv run archon-vision --help
```

## 接入 MCP 客户端

### Claude Code

如果已通过 `uv tool install` 安装：

```bash
claude mcp add -s user archon-vision \
  --env MIMO_API_KEY=your-api-key \
  -- archon-vision
```

直接从本地源码运行：

```bash
claude mcp add -s project archon-vision \
  --env MIMO_API_KEY=your-api-key \
  -- uv run --directory /absolute/path/to/archon/vision archon-vision
```

检查连接状态：

```bash
claude mcp list
```

客户端会按需自动启动和停止默认的 stdio 进程，无需手动维护常驻服务。

### 通用 JSON 配置

```json
{
  "mcpServers": {
    "archon-vision": {
      "command": "archon-vision",
      "env": {
        "MIMO_API_KEY": "your-api-key"
      }
    }
  }
}
```

如果客户端找不到命令，请将 `command` 改为 `archon-vision` 的绝对路径。

## `analyze_image` 工具

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `image_source` | string | 必填 | HTTP/HTTPS URL、JPEG/PNG data URI 或授权的本地路径 |
| `prompt` | string | `请详细描述这张图片的内容` | 聚焦的视觉问题，最长 4000 字符 |

调用示例：

```text
analyze_image(image_source="https://example.com/chart.png", prompt="读取图例和每个柱形的数值")
analyze_image(image_source="@/project/screenshots/error.png", prompt="完整读取错误信息并指出失败组件")
analyze_image(image_source="C:/work/ui.png", prompt="比较左右两栏，只描述可见差异")
```

好的 `prompt` 应描述当前任务需要的具体证据，而不是总让模型泛泛描述整张图。例如要求读取某一区域文字、比较两个 UI 状态、提取图表标签与数值，或明确标记无法可靠辨认的内容。

### 成功响应

工具返回 JSON 字符串：

```json
{
  "image_url": "/project/screenshots/error.png",
  "prompt": "完整读取错误信息并指出失败组件",
  "understanding": "错误对话框显示……",
  "model": "mimo-v2.5"
}
```

对于 Base64 输入，`image_url` 只返回 `data:image/png;base64,<omitted>`，不会重复完整载荷。

### 失败响应

```json
{
  "image_url": "/project/screenshots/error.png",
  "prompt": "读取错误信息",
  "understanding": "",
  "model": "mimo-v2.5",
  "error": "Image source error: ...",
  "error_code": "invalid_image_source"
}
```

常见 `error_code`：

| 代码 | 含义 |
|---|---|
| `configuration_error` | `MIMO_API_KEY` 未配置 |
| `invalid_image_source` | 图片来源、格式、路径权限或大小不合法 |
| `invalid_prompt` | 视觉问题为空或过长 |
| `authentication_error` | MiMo API 鉴权失败 |
| `rate_limited` | MiMo API 返回 429 |
| `upstream_error` | MiMo 网络或其他 HTTP 错误 |
| `invalid_provider_response` | 上游响应不符合契约 |
| `provider_error` | provider 出现未预期异常 |

## Claude Code 模型策略

Claude Code 启用 Tool Search 时，`analyze_image` 的名称与工具描述足以支持按语义发现。为了提高实际调用稳定性，可在项目 `CLAUDE.md` 中加入明确的回答前置条件：

```markdown
## Image Understanding

`analyze_image` is the only permitted tool for understanding image pixels.
Do not infer visual content from filenames, paths, metadata, alt text, or
surrounding prose, and do not use file-reading tools as substitutes.

Before answering, you MUST call `analyze_image` when any of these conditions
applies:

- The user provides or references an image, screenshot, chart, diagram, scan,
  mockup, or visual attachment and asks about its contents.
- The task requires OCR, reading an error dialog, extracting chart values,
  identifying objects, reviewing a layout, or comparing visible states.
- A conclusion depends on details that must be observed in image pixels.

Use a focused prompt that requests the exact visual evidence needed for the
task. Treat the result as model-generated analysis rather than infallible truth.
Preserve uncertainty around small text, exact numbers, and ambiguous details.

If `analyze_image` is unavailable or returns an error, report that limitation.
Do not silently replace it with another image-understanding mechanism or claim
that unobserved visual details were verified.
```

`All image understanding tasks must use analyze_image` 只限定工具路由，没有清楚定义触发条件。上面的 `Before answering, you MUST call` 和具体视觉任务列表共同建立调用门槛，但模型执行提示词仍具有概率性，并可能受更高优先级指令影响。

## 配置

配置查找顺序为 `vision/.env`、当前工作目录 `.env`、`~/.config/archon-vision/.env`，命中第一个后停止。MCP 客户端或 Shell 显式传入的环境变量优先于 `.env`。

### MiMo 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `MIMO_API_KEY` | 空 | MiMo API Key；成功分析必需 |
| `MIMO_BASE_URL` | `https://api.xiaomimimo.com/v1` | OpenAI 兼容 API 根地址 |
| `MIMO_MODEL` | `mimo-v2.5` | 模型名称 |
| `MIMO_TIMEOUT` | `120` | 上游超时，范围 1–300 秒 |
| `MIMO_MAX_TOKENS` | `2048` | 最大输出 token，范围 1–16384 |
| `MIMO_MAX_IMAGE_MB` | `50` | 图片解码后最大大小，范围 1–50 MB |
| `MIMO_ALLOWED_DIR` | MCP 进程当前工作目录 | 允许读取本地图片的根目录 |

本地文件在解析符号链接后的真实路径必须位于 `MIMO_ALLOWED_DIR` 内。Linux/macOS 可使用 `/home/name/Pictures`；Windows 建议在 `.env` 中使用 `C:/Users/name/Pictures`。URL 图片和 data URI 不受本地目录设置影响。

### MCP 服务配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `ARCHON_VISION_TRANSPORT` | `stdio` | `stdio`、`streamable-http` 或 `sse` |
| `ARCHON_VISION_HOST` | `127.0.0.1` | HTTP/SSE 监听地址 |
| `ARCHON_VISION_PORT` | `8000` | HTTP/SSE 端口 |
| `ARCHON_VISION_LOG_LEVEL` | `INFO` | 日志等级 |
| `ARCHON_VISION_STREAMABLE_HTTP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `ARCHON_VISION_STATELESS_HTTP` | `false` | 是否使用无状态 HTTP |
| `ARCHON_VISION_SSE_PATH` | `/sse` | SSE 连接路径 |
| `ARCHON_VISION_MESSAGE_PATH` | `/messages/` | SSE 消息路径 |

命令行中的 `--transport`、`--host`、`--port` 会覆盖对应环境变量。

## 运行方式

### stdio（默认）

```bash
archon-vision
```

这是本地 MCP 客户端的推荐方式，不需要手动启动。

### Streamable HTTP

```bash
archon-vision --transport streamable-http --host 127.0.0.1 --port 8000
```

默认端点为 `http://127.0.0.1:8000/mcp`。服务未内置身份认证，不要直接绑定公网地址。

### SSE（兼容模式）

```bash
archon-vision --transport sse --host 127.0.0.1 --port 8000
```

新接入优先使用 stdio 或 Streamable HTTP。

## 测试

单元测试会 mock MiMo API，不读取 `.env` 中的 Key，也不会上传真实图片：

```bash
cd vision
uv sync --group dev
uv run pytest tests -q
```

## 常见问题

### 工具可见但返回 `configuration_error`

为 MCP 客户端配置 `MIMO_API_KEY` 并重新连接。工具保持可见是为了让 Tool Search 能发现能力并返回可诊断错误。

### 本地图片返回 `Access denied`

默认只允许 MCP 进程当前工作目录。将 `MIMO_ALLOWED_DIR` 设为图片所在目录或共同父目录；不要为了方便直接授权整个用户目录或磁盘根目录。

### URL 图片分析失败

MiMo 服务需要能够访问该 URL。仅本机可见、需要登录或即将过期的地址通常无法由上游读取，可改用授权的本地文件路径或 Base64 data URI。

### Windows 兼容性

核心服务、HTTP 客户端和路径处理均使用跨平台 Python API。配置文件中建议使用正斜杠路径。当前仓库尚未配置 Windows CI，因此发布前仍应在 Windows 上执行一次完整测试。

## 项目结构

```text
vision/
├── .env.example
├── README.md
├── pyproject.toml
├── uv.lock
├── server/
│   ├── config.py                 # 环境变量解析与边界校验
│   ├── instructions.py           # server instructions 与工具说明
│   ├── main.py                   # MCP server、传输和 CLI 入口
│   ├── providers/
│   │   ├── __init__.py           # provider 注册表与协议
│   │   └── mimo.py               # 图片校验、API 调用和结果清洗
│   └── tools/
│       └── analyze_image.py      # MCP 工具和稳定响应契约
└── tests/
    ├── test_analyze_image.py
    ├── test_config.py
    ├── test_instructions.py
    ├── test_main.py
    └── test_mimo.py
```
