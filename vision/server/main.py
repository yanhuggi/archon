"""archon-vision MCP server entry point."""

from __future__ import annotations

import argparse
import atexit
import logging
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from mcp.server import MCPServer

from server.config import SUPPORTED_TRANSPORTS, VisionConfig
from server.instructions import SERVER_INSTRUCTIONS
from server.providers import register as register_provider
from server.providers.mimo import MimoVisionProvider
from server.tools.analyze_image import register as register_analyze_image


LOGGER = logging.getLogger(__name__)
SERVER_NAME = "archon-vision"
SERVER_VERSION = "0.1.0"


def load_environment() -> Path | None:
    """Load the first available .env without overriding client settings."""

    candidates = (
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
        Path.home() / ".config/archon-vision/.env",
    )
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            return path
    return None


load_environment()


def create_server(config: VisionConfig | None = None) -> MCPServer:
    """Create an isolated MCP server with one stable public tool."""

    config = config or VisionConfig.from_env()
    provider = MimoVisionProvider(config)
    register_provider("mimo", provider)
    atexit.register(provider.close)

    server = MCPServer(
        name=SERVER_NAME,
        title="Archon Vision",
        description="MCP server for focused image understanding.",
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level=config.log_level,
    )
    register_analyze_image(server, config=config)
    return server


def __getattr__(name: str) -> object:
    """Build the module-level ``mcp`` server only when something asks for it.

    The ``mcp`` CLI discovers a server by looking up a module attribute named
    ``mcp``, ``server``, or ``app``. Constructing it eagerly would make every
    normal ``main()`` startup build a second, unused server and provider, and
    register a stray ``atexit`` close callback.
    """

    if name == "mcp":
        server = create_server()
        globals()["mcp"] = server
        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the archon-vision MCP server")
    parser.add_argument("--transport", choices=SUPPORTED_TRANSPORTS, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def _config_from_args(args: argparse.Namespace, base: VisionConfig) -> VisionConfig:
    port = args.port if args.port is not None else base.port
    if not 1 <= port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    return VisionConfig(
        api_key=base.api_key,
        base_url=base.base_url,
        model=base.model,
        timeout=base.timeout,
        max_completion_tokens=base.max_completion_tokens,
        max_image_size=base.max_image_size,
        allowed_dir=base.allowed_dir,
        transport=args.transport if args.transport is not None else base.transport,
        host=args.host if args.host is not None else base.host,
        port=port,
        log_level=base.log_level,
        streamable_http_path=base.streamable_http_path,
        sse_path=base.sse_path,
        message_path=base.message_path,
        stateless_http=base.stateless_http,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the server; MCP clients normally start the default stdio process."""

    args = _build_parser().parse_args(argv)
    config = _config_from_args(args, VisionConfig.from_env())
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.api_key:
        LOGGER.warning("MIMO_API_KEY is not set; analyze_image will return configuration_error")

    server = create_server(config)
    if config.transport == "stdio":
        server.run(transport="stdio")
    elif config.transport == "sse":
        server.run(
            transport="sse",
            host=config.host,
            port=config.port,
            sse_path=config.sse_path,
            message_path=config.message_path,
        )
    else:
        server.run(
            transport="streamable-http",
            host=config.host,
            port=config.port,
            streamable_http_path=config.streamable_http_path,
            stateless_http=config.stateless_http,
        )


if __name__ == "__main__":
    main()
