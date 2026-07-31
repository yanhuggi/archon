"""Validated runtime configuration for archon-vision."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_IMAGE_MB = 50
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"
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
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    LOGGER.warning("Invalid %s=%r; using %s", name, raw, default)
    return default


def _read_path(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip() or default
    return value if value.startswith("/") else f"/{value}"


def default_allowed_dir() -> Path:
    """Use the MCP client's working directory as the local-file boundary."""

    return Path.cwd().resolve()


@dataclass(frozen=True, slots=True)
class VisionConfig:
    """Configuration shared by the MCP server and MiMo provider."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_completion_tokens: int = DEFAULT_MAX_TOKENS
    max_image_size: int = DEFAULT_MAX_IMAGE_MB * 1024 * 1024
    allowed_dir: Path = field(default_factory=default_allowed_dir)
    transport: str = DEFAULT_TRANSPORT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    stateless_http: bool = False

    @classmethod
    def from_env(cls) -> "VisionConfig":
        api_key = os.environ.get("MIMO_API_KEY", "").strip() or None
        base_url = os.environ.get("MIMO_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            LOGGER.warning("Invalid MIMO_BASE_URL=%r; using the default", base_url)
            base_url = DEFAULT_BASE_URL

        model = os.environ.get("MIMO_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        allowed_raw = os.environ.get("MIMO_ALLOWED_DIR", "").strip()
        allowed_dir = Path(allowed_raw).expanduser().resolve() if allowed_raw else default_allowed_dir()

        transport = os.environ.get("ARCHON_VISION_TRANSPORT", DEFAULT_TRANSPORT).strip().lower()
        if transport not in SUPPORTED_TRANSPORTS:
            LOGGER.warning("Unsupported ARCHON_VISION_TRANSPORT=%r; using stdio", transport)
            transport = DEFAULT_TRANSPORT

        log_level = os.environ.get("ARCHON_VISION_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            LOGGER.warning("Unsupported ARCHON_VISION_LOG_LEVEL=%r; using INFO", log_level)
            log_level = DEFAULT_LOG_LEVEL

        max_image_mb = _read_int("MIMO_MAX_IMAGE_MB", DEFAULT_MAX_IMAGE_MB, 1, 50)
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=_read_float("MIMO_TIMEOUT", DEFAULT_TIMEOUT, 1.0, 300.0),
            max_completion_tokens=_read_int("MIMO_MAX_TOKENS", DEFAULT_MAX_TOKENS, 1, 16384),
            max_image_size=max_image_mb * 1024 * 1024,
            allowed_dir=allowed_dir,
            transport=transport,
            host=os.environ.get("ARCHON_VISION_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
            port=_read_int("ARCHON_VISION_PORT", DEFAULT_PORT, 1, 65535),
            log_level=log_level,
            streamable_http_path=_read_path("ARCHON_VISION_STREAMABLE_HTTP_PATH", "/mcp"),
            sse_path=_read_path("ARCHON_VISION_SSE_PATH", "/sse"),
            message_path=_read_path("ARCHON_VISION_MESSAGE_PATH", "/messages/"),
            stateless_http=_read_bool("ARCHON_VISION_STATELESS_HTTP"),
        )
