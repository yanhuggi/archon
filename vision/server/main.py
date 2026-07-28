"""archon-vision MCP server entry point."""

import atexit
import sys
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env — checks project dir, CWD, then global config
for p in (
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
    Path.home() / ".config/archon-vision/.env",
):
    if p.exists():
        load_dotenv(p, override=True)
        break

from server.providers import register as register_provider
from server.providers.mimo import MimoVisionProvider
from server.tools.analyze_image import register as register_analyze_image

# Register MiMo provider
mimo_provider = MimoVisionProvider()
register_provider("mimo", mimo_provider)

# Ensure HTTP connections are cleaned up on exit
atexit.register(mimo_provider.close)

# Create MCP server
mcp = FastMCP("archon-vision")

# Register the analyze_image tool only if API key is configured
if os.environ.get("MIMO_API_KEY"):
    register_analyze_image(mcp, default_provider="mimo")
else:
    print("Warning: MIMO_API_KEY not set. analyze_image tool will not be registered. "
          "Set MIMO_API_KEY in environment or .env to enable image analysis.",
          file=sys.stderr)


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
