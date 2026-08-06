"""Runtime configuration for the archon-web MCP server.

Configuration is intentionally read from the environment at the point where it
is used.  This keeps the DuckDuckGo client easy to exercise in tests and allows a
long-running process to be configured through a normal ``.env`` file without
introducing a second configuration format.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 2.0
DEFAULT_TIMEOUT = 10
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"

MIN_INTERVAL = 0.0
MAX_INTERVAL = 60.0
MIN_TIMEOUT = 1
MAX_TIMEOUT = 120

SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable-http")


def default_rate_limit_file() -> Path:
    """Return a per-user state file shared by all archon-web processes."""

    if os.name == "nt":
        cache_root = os.environ.get("LOCALAPPDATA")
        base = Path(cache_root) if cache_root else Path.home() / "AppData/Local"
    else:
        cache_root = os.environ.get("XDG_CACHE_HOME")
        base = Path(cache_root) if cache_root else Path.home() / ".cache"
    return base / "archon-web/duckduckgo-rate-limit"


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read and clamp a floating-point environment value."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %.1f", name, raw, default)
        return default

    if value < minimum or value > maximum:
        logger.warning(
            "%s=%s is outside [%.1f, %.1f]; clamping",
            name,
            raw,
            minimum,
            maximum,
        )
        return min(max(value, minimum), maximum)
    return value


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


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Validated settings used by the server and DuckDuckGo provider."""

    interval: float = DEFAULT_INTERVAL
    timeout: int = DEFAULT_TIMEOUT
    proxy: str | None = None
    rate_limit_file: Path = field(default_factory=default_rate_limit_file)
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
        proxy = os.environ.get("ARCHON_WEB_PROXY") or os.environ.get("DDGS_PROXY") or None
        rate_limit_file = Path(
            os.environ.get("ARCHON_WEB_RATE_LIMIT_FILE") or default_rate_limit_file()
        ).expanduser()

        return cls(
            interval=_read_float(
                "ARCHON_WEB_DUCKDUCKGO_INTERVAL",
                DEFAULT_INTERVAL,
                MIN_INTERVAL,
                MAX_INTERVAL,
            ),
            timeout=_read_int("ARCHON_WEB_TIMEOUT", DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT),
            proxy=proxy,
            rate_limit_file=rate_limit_file,
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
