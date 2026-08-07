"""archon-jira MCP server entry point."""

from __future__ import annotations

import argparse
import atexit
import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer

from server.config import SUPPORTED_TRANSPORTS, JiraConfig
from server.instructions import SERVER_INSTRUCTIONS
from server.providers.jira import JiraProvider
from server.tools.add_comment import register as register_add_comment
from server.tools.delete_comment import register as register_delete_comment
from server.tools.export_issue import register as register_export_issue
from server.tools.get_attachment import register as register_get_attachment
from server.tools.get_comments import register as register_get_comments
from server.tools.get_issue import register as register_get_issue
from server.tools.get_transitions import register as register_get_transitions
from server.tools.get_jql_value_suggestions import (
    register as register_get_jql_value_suggestions,
)
from server.tools.search_issues import register as register_search_issues
from server.tools.search_jql_fields import register as register_search_jql_fields
from server.tools.update_comment import register as register_update_comment
from server.tools.update_issue import register as register_update_issue
from server.tools.transition_issue import register as register_transition_issue

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
    atexit.register(provider.close)

    server = MCPServer(
        name=SERVER_NAME,
        title="Archon Jira",
        description="MCP server for Jira issue/comment retrieval, controlled editing, and local exports.",
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level=config.log_level,
    )
    register_search_jql_fields(server, provider=provider)
    register_get_jql_value_suggestions(server, provider=provider)
    register_search_issues(server, provider=provider)
    register_get_issue(server, provider=provider)
    register_get_transitions(server, provider=provider)
    register_get_comments(server, provider=provider)
    register_add_comment(server, provider=provider)
    register_update_comment(server, provider=provider)
    register_delete_comment(server, provider=provider)
    register_update_issue(server, provider=provider)
    register_transition_issue(server, provider=provider)
    register_get_attachment(server, provider=provider)
    register_export_issue(server, config=config, provider=provider)
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
    parser = argparse.ArgumentParser(description="Run the archon-jira MCP server")
    parser.add_argument("--transport", choices=SUPPORTED_TRANSPORTS, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def _config_from_args(args: argparse.Namespace, base: JiraConfig) -> JiraConfig:
    """Apply command-line overrides without mutating the environment.

    ``replace`` keeps every other setting attached to ``base``, so a new config
    field cannot silently fall back to its default when the CLI is used.
    """

    port = args.port if args.port is not None else base.port
    if not 1 <= port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    return replace(
        base,
        transport=args.transport if args.transport is not None else base.transport,
        host=args.host if args.host is not None else base.host,
        port=port,
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
