"""Runtime configuration for the archon-web MCP server.

Configuration is read from the environment at the point where it is used.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"

MIN_TIMEOUT = 1
MAX_TIMEOUT = 120

SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable-http")
SUPPORTED_TAVILY_SEARCH_DEPTHS = ("basic", "advanced")
SUPPORTED_TAVILY_TOPICS = ("general", "news", "finance")


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read and clamp an integer environment value."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %d", name, raw, default)
        return default

    if value < minimum or value > maximum:
        logger.warning(
            "%s=%s is outside [%d, %d]; clamping",
            name,
            raw,
            minimum,
            maximum,
        )
        return min(max(value, minimum), maximum)
    return value


def _read_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    """Read a case-insensitive enum and fall back to its documented default."""

    value = os.environ.get(name, default).strip().lower() or default
    if value not in choices:
        logger.warning("Unsupported %s=%r; using %s", name, value, default)
        return default
    return value


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Validated settings used by the server and Tavily provider."""

    timeout: int = DEFAULT_TIMEOUT
    tavily_api_key: str | None = None
    tavily_search_depth: str = "basic"
    tavily_topic: str = "general"
    transport: str = DEFAULT_TRANSPORT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    stateless_http: bool = False

    @classmethod
    def from_env(cls) -> "WebConfig":
        """Build a validated configuration from ``ARCHON_WEB_*`` variables."""

        transport = os.environ.get("ARCHON_WEB_TRANSPORT", DEFAULT_TRANSPORT).strip().lower()
        if transport not in SUPPORTED_TRANSPORTS:
            logger.warning(
                "Unsupported ARCHON_WEB_TRANSPORT=%r; using %s",
                transport,
                DEFAULT_TRANSPORT,
            )
            transport = DEFAULT_TRANSPORT

        log_level = os.environ.get("ARCHON_WEB_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            logger.warning("Unsupported ARCHON_WEB_LOG_LEVEL=%r; using %s", log_level, DEFAULT_LOG_LEVEL)
            log_level = DEFAULT_LOG_LEVEL

        host = os.environ.get("ARCHON_WEB_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
        return cls(
            timeout=_read_int("ARCHON_WEB_TIMEOUT", DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT),
            tavily_api_key=_read_secret("TAVILY_API_KEY", "ARCHON_WEB_TAVILY_API_KEY"),
            tavily_search_depth=_read_choice(
                "TAVILY_SEARCH_DEPTH", "basic", SUPPORTED_TAVILY_SEARCH_DEPTHS
            ),
            tavily_topic=_read_choice("TAVILY_TOPIC", "general", SUPPORTED_TAVILY_TOPICS),
            transport=transport,
            host=host,
            port=_read_int("ARCHON_WEB_PORT", DEFAULT_PORT, 1, 65535),
            log_level=log_level,
            streamable_http_path=(
                os.environ.get("ARCHON_WEB_STREAMABLE_HTTP_PATH", "/mcp").strip() or "/mcp"
            ),
            sse_path=os.environ.get("ARCHON_WEB_SSE_PATH", "/sse").strip() or "/sse",
            message_path=(
                os.environ.get("ARCHON_WEB_MESSAGE_PATH", "/messages/").strip() or "/messages/"
            ),
            stateless_http=_as_bool(os.environ.get("ARCHON_WEB_STATELESS_HTTP"), default=False),
        )


def _as_bool(value: str | None, *, default: bool) -> bool:
    """Parse a conventional boolean environment value."""

    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean value %r; using %s", value, default)
    return default


def _read_secret(*names: str) -> str | None:
    """Return the first non-blank secret without altering its value."""

    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value
    return None
