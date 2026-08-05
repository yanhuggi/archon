"""MiMo image-understanding provider."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import stat
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx

from server.config import VisionConfig


LOGGER = logging.getLogger(__name__)
DEFAULT_PROMPT = "请详细描述这张图片的内容"
_IMAGE_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_DATA_URI_RE = re.compile(r"^data:(image/(?:jpeg|png));base64,([A-Za-z0-9+/]*={0,2})$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_BASE64_WHITESPACE_RE = re.compile(r"\s+")

# Opening a FIFO or other blocking special file must never park the calling
# thread: the MCP tool runs in a bounded worker pool, so a parked thread is a
# permanently lost worker. O_NONBLOCK makes such an open return immediately;
# the fstat S_ISREG check below then rejects the file.
_NONBLOCK_FLAG = getattr(os, "O_NONBLOCK", 0)


def display_image_source(source: str) -> str:
    """Return a compact, non-payload representation for MCP responses."""

    value = source[1:] if source.startswith("@") else source
    if value.startswith("data:"):
        header, separator, _payload = value.partition(",")
        return f"{header},<omitted>" if separator else "data:<invalid>"
    return value


def _get_allowed_dir(config: VisionConfig | None = None) -> Path | None:
    """Return the optional configured local-file boundary."""

    return (config or VisionConfig.from_env()).allowed_dir


def _validate_path_safe(resolved: Path, config: VisionConfig | None = None) -> None:
    allowed = _get_allowed_dir(config)
    if allowed is not None and not resolved.is_relative_to(allowed):
        raise ValueError(
            f"Access denied: path '{resolved}' is outside the allowed directory "
            f"'{allowed}'. Set MIMO_ALLOWED_DIR to the required directory."
        )


def _local_path_from_file_url(source: str) -> str:
    parsed = urlparse(source)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        path = url2pathname(unquote(parsed.path))
        return f"//{parsed.netloc}{path}" if parsed.netloc else path
    if parsed.netloc not in {"", "localhost"}:
        raise ValueError("Remote file URLs are not supported")
    return unquote(parsed.path)


def _clear_nonblock(fd: int) -> None:
    """Drop O_NONBLOCK once the descriptor is known to be a regular file.

    Regular-file reads ignore the flag, but clearing it keeps ``os.read``
    semantics identical to a plain blocking open.
    """

    if not _NONBLOCK_FLAG:  # pragma: no cover - Windows has no O_NONBLOCK
        return
    try:
        import fcntl

        current = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, current & ~_NONBLOCK_FLAG)
    except (ImportError, OSError) as exc:  # pragma: no cover - defensive
        LOGGER.debug("Could not clear O_NONBLOCK: %s", exc)


def _validate_image_signature(data: bytes, mime: str) -> None:
    if mime == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("File content does not match JPEG format")
    if mime == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("File content does not match PNG format")


def process_image_source(source: str, config: VisionConfig | None = None) -> str:
    """Validate an image source and convert local files to base64 data URIs."""

    config = config or VisionConfig.from_env()
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Image source must not be empty")
    source = source.strip()
    if source.startswith("@"):
        source = source[1:]

    if source.startswith("data:"):
        # MIME encoders commonly wrap base64 at 76 columns. Such a payload is
        # still unambiguous, so strip the whitespace instead of reporting it as
        # an unsupported image format.
        header, separator, payload = source.partition(",")
        if separator and _BASE64_WHITESPACE_RE.search(payload):
            source = f"{header},{_BASE64_WHITESPACE_RE.sub('', payload)}"
        match = _DATA_URI_RE.fullmatch(source)
        if match is None:
            raise ValueError("Invalid data URI; only base64 JPEG and PNG images are supported")
        mime, encoded = match.groups()
        if not encoded:
            raise ValueError("Empty data URI payload")
        max_encoded_size = ((config.max_image_size + 2) // 3) * 4
        if len(encoded) > max_encoded_size:
            raise ValueError(
                f"Data URI payload too large. Maximum decoded size: "
                f"{config.max_image_size / 1024 / 1024:.0f} MB."
            )
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 image payload") from exc
        if len(decoded) > config.max_image_size:
            raise ValueError(
                f"Data URI payload too large. Maximum decoded size: "
                f"{config.max_image_size / 1024 / 1024:.0f} MB."
            )
        _validate_image_signature(decoded, mime)
        return source

    if source.startswith("file://"):
        source = _local_path_from_file_url(source)
    else:
        parsed = urlparse(source)
        is_windows_path = os.name == "nt" and _WINDOWS_DRIVE_PATH_RE.match(source) is not None
        if parsed.scheme and not is_windows_path:
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Image URL must use http or https")
            return source

    resolved = Path(os.path.realpath(source))
    _validate_path_safe(resolved, config)

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | _NONBLOCK_FLAG
    try:
        fd = os.open(str(resolved), flags)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Local image file does not exist: {source}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot open local image: {exc}") from exc

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError(f"Not a regular file: {resolved}")
        _clear_nonblock(fd)

        # Confirm that validation and opening resolved to the same file.
        current_stat = os.stat(resolved)
        if (opened_stat.st_dev, opened_stat.st_ino) != (current_stat.st_dev, current_stat.st_ino):
            raise ValueError(f"File changed while being opened: {resolved}")
        _validate_path_safe(Path(os.path.realpath(resolved)), config)

        ext = resolved.suffix.lower()
        if not ext:
            raise ValueError(
                f"Local file has no extension: {source!r}. Supported formats: "
                f"{', '.join(_IMAGE_EXT_MIME)}"
            )
        mime = _IMAGE_EXT_MIME.get(ext)
        if mime is None:
            raise ValueError(f"Unsupported image format: {ext}. Supported: {', '.join(_IMAGE_EXT_MIME)}")
        if opened_stat.st_size > config.max_image_size:
            raise ValueError(
                f"Image file too large: {opened_stat.st_size / 1024 / 1024:.1f} MB. "
                f"Maximum allowed: {config.max_image_size / 1024 / 1024:.0f} MB."
            )

        chunks: list[bytes] = []
        remaining = opened_stat.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        image_data = b"".join(chunks)
        if len(image_data) != opened_stat.st_size:
            raise ValueError("Local image changed or could not be read completely")
        _validate_image_signature(image_data, mime)
    finally:
        os.close(fd)

    encoded = base64.b64encode(image_data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _error_response(
    image_source: str,
    prompt: str,
    model: str,
    code: str,
    message: str,
) -> str:
    return json.dumps(
        {
            "image_url": display_image_source(image_source),
            "prompt": prompt,
            "understanding": "",
            "model": model,
            "error": message,
            "error_code": code,
        },
        ensure_ascii=False,
    )


class MimoVisionProvider:
    """Image understanding through MiMo's OpenAI-compatible endpoint."""

    def __init__(self, config: VisionConfig | None = None) -> None:
        self._fixed_config = config
        self._http_client: httpx.Client | None = None
        self._client_timeout: float | None = None
        self._client_lock = threading.Lock()

    def _config(self) -> VisionConfig:
        return self._fixed_config or VisionConfig.from_env()

    def close(self) -> None:
        with self._client_lock:
            if self._http_client is not None:
                self._http_client.close()
                self._http_client = None
                self._client_timeout = None

    def _get_client(self, config: VisionConfig | None = None) -> httpx.Client:
        config = config or self._config()
        with self._client_lock:
            if self._http_client is None or self._client_timeout != config.timeout:
                if self._http_client is not None:
                    self._http_client.close()
                self._http_client = httpx.Client(timeout=config.timeout)
                self._client_timeout = config.timeout
            return self._http_client

    def _api_url(self, config: VisionConfig | None = None) -> str:
        return (config or self._config()).base_url.rstrip("/") + "/chat/completions"

    def _model(self, **kwargs: object) -> str:
        value = kwargs.get("model")
        return str(value).strip() if value else self._config().model

    def understand(self, image_source: str, prompt: str = DEFAULT_PROMPT, **kwargs: object) -> str:
        """Analyze one validated image and return a stable JSON envelope."""

        config = self._config()
        model = self._model(**kwargs)
        prompt = prompt.strip() if isinstance(prompt, str) else ""
        if not prompt:
            return _error_response(image_source, prompt, model, "invalid_prompt", "prompt must not be empty")
        if not config.api_key:
            return _error_response(
                image_source,
                prompt,
                model,
                "configuration_error",
                "MIMO_API_KEY is not configured",
            )

        try:
            processed_url = process_image_source(image_source, config)
        except (FileNotFoundError, ValueError, OSError) as exc:
            return _error_response(
                image_source,
                prompt,
                model,
                "invalid_image_source",
                f"Image source error: {exc}",
            )

        max_tokens_value = kwargs.get("max_tokens", config.max_completion_tokens)
        try:
            max_tokens = min(max(int(max_tokens_value), 1), 16384)
        except (TypeError, ValueError, OverflowError):
            max_tokens = config.max_completion_tokens
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": processed_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_completion_tokens": max_tokens,
        }

        try:
            response = self._get_client(config).post(
                self._api_url(config),
                headers={
                    "api-key": config.api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                # A 200 with a non-JSON body (gateway error page, truncated
                # stream) is an upstream contract violation, not a local bug.
                LOGGER.warning("MiMo returned a non-JSON body: %s", exc)
                return _error_response(
                    image_source,
                    prompt,
                    model,
                    "invalid_provider_response",
                    "MiMo returned a response that is not valid JSON",
                )
            return self._format(image_source, prompt, model, data)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = "authentication_error" if status in {401, 403} else "rate_limited" if status == 429 else "upstream_error"
            LOGGER.warning("MiMo returned HTTP %d", status)
            return _error_response(image_source, prompt, model, code, f"MiMo returned HTTP {status}")
        except httpx.RequestError as exc:
            LOGGER.warning("MiMo request failed: %s", exc)
            return _error_response(image_source, prompt, model, "upstream_error", f"Request failed: {exc}")
        except Exception as exc:
            LOGGER.exception("MiMo image analysis failed")
            return _error_response(
                image_source,
                prompt,
                model,
                "provider_error",
                f"{type(exc).__name__}: {exc}",
            )

    def _format(self, image_source: str, prompt: str, model: str, data: object) -> str:
        if not isinstance(data, Mapping):
            return _error_response(
                image_source,
                prompt,
                model,
                "invalid_provider_response",
                "MiMo returned a non-object response",
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return _error_response(
                image_source,
                prompt,
                model,
                "invalid_provider_response",
                "MiMo response contains no usable choice",
            )
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            return _error_response(
                image_source,
                prompt,
                model,
                "invalid_provider_response",
                "MiMo response contains no message",
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return _error_response(
                image_source,
                prompt,
                model,
                "invalid_provider_response",
                "MiMo response contains no analysis text",
            )

        result: dict[str, Any] = {
            "image_url": display_image_source(image_source),
            "prompt": prompt,
            "understanding": content.strip(),
            "model": model,
        }
        # A length-capped reply is a partial answer. Flag it so callers do not
        # treat half-read OCR text or a cut-off value as the complete result.
        if choices[0].get("finish_reason") == "length":
            result["truncated"] = True
            result["finish_reason"] = "length"
        return json.dumps(result, ensure_ascii=False)
