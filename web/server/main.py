"""archon-web MCP server entry point."""

import sys
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer

# Load .env — checks project dir, CWD, then global config
for p in (
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
    Path.home() / ".config/archon-web/.env",
):
    if p.exists():
        load_dotenv(p, override=True)
        break

from server.providers import is_registered, register as register_provider
from server.providers.tavily import TavilyProvider
from server.providers.deepseek import DeepSeekProvider
from server.tools.web_search import register as register_web_search

# Register built-in providers
register_provider("tavily", TavilyProvider())
register_provider("deepseek", DeepSeekProvider())

# DuckDuckGo — opt-in, no API key required
duckduckgo_enabled = os.environ.get("ARCHON_WEB_DUCKDUCKGO_ENABLED", "").lower() in ("true", "1", "yes")
if duckduckgo_enabled:
    try:
        from server.providers.duckduckgo import DuckDuckGoProvider  # noqa: PLC0415
        register_provider("duckduckgo", DuckDuckGoProvider())
    except ModuleNotFoundError:
        print("Warning: ARCHON_WEB_DUCKDUCKGO_ENABLED is set but duckduckgo-search is not installed. "
              "Install it with: pip install \"archon-web[duckduckgo]\"", file=sys.stderr)

# Create MCP server
mcp = MCPServer("archon-web")

# Determine default provider:
#   1. ARCHON_WEB_PROVIDER env var (explicit user choice)
#   2. Auto-detect: tavily → deepseek → duckduckgo
has_tavily = bool(os.environ.get("TAVILY_API_KEY"))
has_deepseek = bool(os.environ.get("DEEPSEEK_API_KEY"))
has_duckduckgo = duckduckgo_enabled and is_registered("duckduckgo")

provider_available = {"tavily": has_tavily, "deepseek": has_deepseek, "duckduckgo": has_duckduckgo}

explicit = os.environ.get("ARCHON_WEB_PROVIDER")
if explicit and explicit in provider_available and provider_available[explicit]:
    default_provider = explicit
else:
    if explicit:
        # User specified a provider but it's not available
        available = [k for k, v in provider_available.items() if v]
        msg = f"Warning: ARCHON_WEB_PROVIDER={explicit} specified but not configured."
        if available:
            msg += f" Falling back to {available[0]}. Available: {available}"
            print(msg, file=sys.stderr)
        else:
            msg += " No search tool will be registered."
            print(msg, file=sys.stderr)

    if has_tavily:
        default_provider = "tavily"
    elif has_deepseek:
        default_provider = "deepseek"
    elif has_duckduckgo:
        default_provider = "duckduckgo"
    else:
        default_provider = "tavily"

if has_tavily or has_deepseek or has_duckduckgo:
    register_web_search(mcp, default_provider=default_provider)


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
