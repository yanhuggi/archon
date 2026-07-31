"""archon-jira MCP server entry point."""

from __future__ import annotations

import argparse
import atexit
import logging
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer

from server.config import SUPPORTED_TRANSPORTS, JiraConfig
from server.instructions import SERVER_INSTRUCTIONS
from server.providers import register as register_provider
from server.providers.jira import JiraProvider
from server.tools.export_issue import register as register_export_issue
from server.tools.get_attachment import register as register_get_attachment
from server.tools.get_comments import register as register_get_comments
from server.tools.get_issue import register as register_get_issue
from server.tools.get_jql_value_suggestions import (
    register as register_get_jql_value_suggestions,
)
from server.tools.search_issues import register as register_search_issues
from server.tools.search_jql_fields import register as register_search_jql_fields

LOGGER = logging.getLogger(__name__)
SERVER_NAME = "archon-jira"
SERVER_VERSION = "0.1.0"


def load_environment() -> Path | None:
    """Load the first available .env without overriding client settings."""

    candidates = (
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
        Path.home() / ".config/archon-jira/.env",
    )
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            return path
    return None


load_environment()


def create_server(config: JiraConfig | None = None) -> MCPServer:
    """Create an isolated MCP server with a stable public tool surface."""

    config = config or JiraConfig.from_env()
    provider = JiraProvider(config)
    register_provider("jira", provider)
    atexit.register(provider.close)

    server = MCPServer(
        name=SERVER_NAME,
        title="Archon Jira",
        description="MCP server for Jira issue retrieval and controlled local exports.",
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level=config.log_level,
    )
    register_search_jql_fields(server)
    register_get_jql_value_suggestions(server)
    register_search_issues(server)
    register_get_issue(server)
    register_get_comments(server)
    register_get_attachment(server)
    register_export_issue(server, config=config)
    return server


mcp = create_server()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the archon-jira MCP server")
    parser.add_argument("--transport", choices=SUPPORTED_TRANSPORTS, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def _config_from_args(args: argparse.Namespace, base: JiraConfig) -> JiraConfig:
    port = args.port if args.port is not None else base.port
    if not 1 <= port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    return JiraConfig(
        url=base.url,
        username=base.username,
        password=base.password,
        timeout=base.timeout,
        max_attachment_size=base.max_attachment_size,
        output_dir=base.output_dir,
        allow_overwrite=base.allow_overwrite,
        jql_disk_cache_enabled=base.jql_disk_cache_enabled,
        jql_cache_dir=base.jql_cache_dir,
        jql_field_refresh_interval=base.jql_field_refresh_interval,
        jql_value_refresh_interval=base.jql_value_refresh_interval,
        jql_cache_max_stale=base.jql_cache_max_stale,
        jql_value_cache_max_entries=base.jql_value_cache_max_entries,
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
    config = _config_from_args(args, JiraConfig.from_env())
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.is_configured:
        LOGGER.warning("Jira credentials are incomplete; tools will return configuration_error")

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
