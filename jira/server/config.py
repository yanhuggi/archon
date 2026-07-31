"""Validated runtime configuration for archon-jira."""

from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_JQL_FIELD_REFRESH_INTERVAL = 15 * 60
DEFAULT_JQL_VALUE_REFRESH_INTERVAL = 30 * 60
DEFAULT_JQL_CACHE_MAX_STALE = 7 * 24 * 60 * 60
DEFAULT_JQL_VALUE_CACHE_MAX_ENTRIES = 500
SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable-http")


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using %d", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        LOGGER.warning("%s=%s is outside [%d, %d]; clamping", name, raw, minimum, maximum)
        return min(max(value, minimum), maximum)
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using %.1f", name, raw, default)
        return default
    if not math.isfinite(value):
        LOGGER.warning("Invalid %s=%r; using %.1f", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        LOGGER.warning("%s=%s is outside [%.1f, %.1f]; clamping", name, raw, minimum, maximum)
        return min(max(value, minimum), maximum)
    return value


def _read_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    LOGGER.warning("Invalid %s=%r; using %s", name, raw, default)
    return default


def _read_path(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip() or default
    return value if value.startswith("/") else f"/{value}"


def default_output_dir() -> Path:
    return Path.cwd().resolve()


def default_jql_cache_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return (root / "archon-jira" / "Cache").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Caches" / "archon-jira").resolve()
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return (root / "archon-jira").resolve()


@dataclass(frozen=True, slots=True)
class JiraConfig:
    """Configuration shared by the MCP server and Jira provider."""

    url: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    max_attachment_size: int = DEFAULT_MAX_ATTACHMENT_SIZE
    output_dir: Path = field(default_factory=default_output_dir)
    allow_overwrite: bool = False
    jql_disk_cache_enabled: bool = True
    jql_cache_dir: Path = field(default_factory=default_jql_cache_dir)
    jql_field_refresh_interval: int = DEFAULT_JQL_FIELD_REFRESH_INTERVAL
    jql_value_refresh_interval: int = DEFAULT_JQL_VALUE_REFRESH_INTERVAL
    jql_cache_max_stale: int = DEFAULT_JQL_CACHE_MAX_STALE
    jql_value_cache_max_entries: int = DEFAULT_JQL_VALUE_CACHE_MAX_ENTRIES
    transport: str = DEFAULT_TRANSPORT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    stateless_http: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.username and self.password)

    @classmethod
    def from_env(cls) -> JiraConfig:
        raw_url = os.environ.get("JIRA_URL", "").strip().rstrip("/")
        url: str | None = raw_url or None
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
                LOGGER.warning("Invalid JIRA_URL=%r; treating Jira as unconfigured", raw_url)
                url = None

        output_raw = os.environ.get("JIRA_ALLOWED_OUTPUT_DIR", "").strip()
        output_dir = Path(output_raw).expanduser().resolve() if output_raw else default_output_dir()
        cache_raw = os.environ.get("JIRA_JQL_CACHE_DIR", "").strip()
        cache_dir = Path(cache_raw).expanduser().resolve() if cache_raw else default_jql_cache_dir()

        transport = os.environ.get("ARCHON_JIRA_TRANSPORT", DEFAULT_TRANSPORT).strip().lower()
        if transport not in SUPPORTED_TRANSPORTS:
            LOGGER.warning("Unsupported ARCHON_JIRA_TRANSPORT=%r; using stdio", transport)
            transport = DEFAULT_TRANSPORT

        log_level = os.environ.get("ARCHON_JIRA_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            LOGGER.warning("Unsupported ARCHON_JIRA_LOG_LEVEL=%r; using INFO", log_level)
            log_level = DEFAULT_LOG_LEVEL

        return cls(
            url=url,
            username=os.environ.get("JIRA_USERNAME", "").strip() or None,
            password=os.environ.get("JIRA_PASSWORD") or None,
            timeout=_read_float("JIRA_TIMEOUT", DEFAULT_TIMEOUT, 1.0, 300.0),
            max_attachment_size=_read_int(
                "JIRA_MAX_ATTACHMENT_SIZE",
                DEFAULT_MAX_ATTACHMENT_SIZE,
                1,
                1024 * 1024 * 1024,
            ),
            output_dir=output_dir,
            allow_overwrite=_read_bool("JIRA_ALLOW_OVERWRITE"),
            jql_disk_cache_enabled=_read_bool("JIRA_JQL_DISK_CACHE", True),
            jql_cache_dir=cache_dir,
            jql_field_refresh_interval=_read_int(
                "JIRA_JQL_FIELD_REFRESH_INTERVAL",
                DEFAULT_JQL_FIELD_REFRESH_INTERVAL,
                0,
                30 * 24 * 60 * 60,
            ),
            jql_value_refresh_interval=_read_int(
                "JIRA_JQL_VALUE_REFRESH_INTERVAL",
                DEFAULT_JQL_VALUE_REFRESH_INTERVAL,
                0,
                30 * 24 * 60 * 60,
            ),
            jql_cache_max_stale=_read_int(
                "JIRA_JQL_CACHE_MAX_STALE",
                DEFAULT_JQL_CACHE_MAX_STALE,
                0,
                365 * 24 * 60 * 60,
            ),
            jql_value_cache_max_entries=_read_int(
                "JIRA_JQL_VALUE_CACHE_MAX_ENTRIES",
                DEFAULT_JQL_VALUE_CACHE_MAX_ENTRIES,
                1,
                10_000,
            ),
            transport=transport,
            host=os.environ.get("ARCHON_JIRA_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
            port=_read_int("ARCHON_JIRA_PORT", DEFAULT_PORT, 1, 65535),
            log_level=log_level,
            streamable_http_path=_read_path("ARCHON_JIRA_STREAMABLE_HTTP_PATH", "/mcp"),
            sse_path=_read_path("ARCHON_JIRA_SSE_PATH", "/sse"),
            message_path=_read_path("ARCHON_JIRA_MESSAGE_PATH", "/messages/"),
            stateless_http=_read_bool("ARCHON_JIRA_STATELESS_HTTP"),
        )
