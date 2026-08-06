"""archon-web MCP server entry point."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from mcp.server import MCPServer

from server.config import SUPPORTED_TRANSPORTS, WebConfig
from server.instructions import SERVER_INSTRUCTIONS
from server.providers.duckduckgo import DuckDuckGoProvider
from server.tools.web_search import register as register_web_search


SERVER_NAME = "archon-web"
SERVER_VERSION = "0.1.0"


def load_environment() -> Path | None:
    """Load the first project/local configuration file that exists.

    ``override=False`` deliberately gives explicitly supplied process
    environment variables precedence over values in ``.env``. This is safer
    for MCP clients, which commonly pass secrets and deployment settings via
    their own environment.
    """

    candidates = (
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
        Path.home() / ".config/archon-web/.env",
    )
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            return path
    return None


# Load .env before constructing the server, but keep registration in a factory
# so tests and embedders can create an isolated MCP server instance.
load_environment()


def create_server(config: WebConfig | None = None) -> MCPServer:
    """Create an isolated MCP server with one stable public tool."""

    config = config or WebConfig.from_env()
    provider = DuckDuckGoProvider(config)

    server = MCPServer(
        name=SERVER_NAME,
        title="Archon Web Search",
        description="MCP server for focused public-web searches.",
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level=config.log_level,
    )
    register_web_search(server, provider=provider)
    return server


def __getattr__(name: str) -> object:
    """Build the module-level ``mcp`` server only when something asks for it.

    The ``mcp`` CLI discovers a server by looking up a module attribute named
    ``mcp``, ``server``, or ``app``. Constructing it eagerly would make every
    normal ``main()`` startup build a second, unused server and provider.
    """

    if name == "mcp":
        server = create_server()
        globals()["mcp"] = server
        return server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the archon-web MCP server")
    parser.add_argument(
        "--transport",
        choices=SUPPORTED_TRANSPORTS,
        default=None,
        help="MCP transport (default: ARCHON_WEB_TRANSPORT or stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP bind address (default: ARCHON_WEB_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP port (default: ARCHON_WEB_PORT or 8000)",
    )
    return parser


def _config_from_args(args: argparse.Namespace, base: WebConfig) -> WebConfig:
    """Apply command-line overrides without mutating the environment."""

    values = {
        "transport": args.transport if args.transport is not None else base.transport,
        "host": args.host if args.host is not None else base.host,
        "port": args.port if args.port is not None else base.port,
    }
    if not 1 <= values["port"] <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    return WebConfig(
        interval=base.interval,
        timeout=base.timeout,
        proxy=base.proxy,
        rate_limit_file=base.rate_limit_file,
        transport=values["transport"],
        host=values["host"],
        port=values["port"],
        log_level=base.log_level,
        streamable_http_path=base.streamable_http_path,
        sse_path=base.sse_path,
        message_path=base.message_path,
        stateless_http=base.stateless_http,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the MCP server, using stdio by default."""

    args = _build_parser().parse_args(argv)
    config = _config_from_args(args, WebConfig.from_env())
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Use a fresh instance so command-line configuration (especially log level)
    # is reflected in the low-level MCP server metadata/settings.
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
