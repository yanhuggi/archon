"""MiMo vision provider — image understanding via 小米 MiMo API."""

import base64
import json
import os
import stat
import sys
import threading
from pathlib import Path
from typing import Any

import httpx

# MiMo 官方 BASE_URL（OpenAI 兼容）: https://api.xiaomimimo.com/v1
# 代码自动拼接 /chat/completions 得到完整 API 路径。
# 可通过 MIMO_BASE_URL 环境变量覆盖。
_MIMO_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5"
DEFAULT_PROMPT = "请详细描述这张图片的内容"

# MiMo API limit: 50 MB per image (raw file size, before Base64 encoding).
# Base64 expands payload by ~1.37×, but the limit applies to the original
# file on disk, not the encoded data URI. Keep this as-is.
_MAX_IMAGE_SIZE = 50 * 1024 * 1024

# Max size for inline data URIs (base64-decoded size) — prevents malicious
# or accidentally-huge inline payloads from being processed.
_MAX_DATA_URI_SIZE = 20 * 1024 * 1024

# Supported image formats and their MIME types
_IMAGE_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# Warning shown when a local file is uploaded to a third-party API
_LOCAL_FILE_UPLOAD_WARNING = (
    "You are about to read a local file and send its content to a third-party API "
    "(小米 MiMo). Only proceed if the file does not contain sensitive information."
)


def _get_allowed_dir() -> Path:
    """Return the allowed base directory for local file reads.

    Reads MIMO_ALLOWED_DIR from environment at call time (not import time),
    so runtime changes to the env var take effect.

    When MIMO_ALLOWED_DIR is set, only paths under that directory are allowed.
    When it is not set, path restriction is disabled — any readable local file
    can be processed. This is a local-only MCP tool; the user has full control
    over which files are passed to it.
    """
    raw = os.environ.get("MIMO_ALLOWED_DIR", "").strip()
    if raw:
        return Path(os.path.realpath(raw))
    return Path("/")


def _validate_path_safe(resolved: Path) -> None:
    """Validate that a resolved path is within the allowed directory.

    Args:
        resolved: A real, resolved absolute Path.

    Raises:
        ValueError: If the path escapes the allowed directory.
    """
    allowed = _get_allowed_dir()
    if not resolved.is_relative_to(allowed):
        raise ValueError(
            f"Access denied: path '{resolved}' is outside the allowed directory "
            f"'{allowed}'. Set MIMO_ALLOWED_DIR to a broader scope if needed."
        )


def process_image_source(source: str) -> str:
    """Normalize an image source to a URL or data URI usable by the API.

    Handles three input types:
    1. HTTP/HTTPS URLs → pass through as-is
    2. Base64 data URIs → pass through as-is (with size validation)
    3. Local file paths → resolve, validate scope, read, encode as base64 data URI

    Also strips a leading ``@`` prefix if present (common in MCP tool calls).

    SECURITY: Local files are read and encoded, then sent to a third-party API
    (小米 MiMo). This is deliberate — the caller is responsible for ensuring
    the file does not contain sensitive information.

    Args:
        source: Image URL, data URI, or local file path.

    Returns:
        A URL string (http/https) or base64 data URI (data:image/...;base64,...).

    Raises:
        FileNotFoundError: If a local file path does not exist.
        ValueError: If the file format is not supported, path escapes the
                    allowed directory, or the data URI is too large.
    """
    # Strip @ prefix if present (common from MCP clients like Claude Desktop)
    if source.startswith("@"):
        source = source[1:]

    # Already a data URI — validate size and pass through
    if source.startswith("data:"):
        try:
            header, _, encoded = source.partition(",")
            if not encoded:
                raise ValueError("Empty data URI payload")
            # Rough base64 decoded size estimate: len(encoded) * 0.75
            decoded_size = int(len(encoded) * 0.75)
            if decoded_size > _MAX_DATA_URI_SIZE:
                raise ValueError(
                    f"Data URI payload too large: ~{decoded_size / 1024 / 1024:.1f} MB. "
                    f"Maximum allowed: {_MAX_DATA_URI_SIZE / 1024 / 1024:.0f} MB."
                )
        except (ValueError, IndexError):
            raise ValueError(f"Invalid data URI format")
        return source

    # HTTP/HTTPS URL — pass through (MiMo API accepts URLs directly)
    if source.startswith(("http://", "https://")):
        return source

    # file:// URL — extract local path and process as local file
    if source.startswith("file://"):
        import urllib.parse
        source = urllib.parse.urlparse(source).path
        # Fall through to local file handling below

    # Local file path — resolve, validate scope, warn, then open
    print(_LOCAL_FILE_UPLOAD_WARNING, file=sys.stderr)
    resolved = Path(os.path.realpath(source))

    # Validate path safety BEFORE any format-specific checks
    _validate_path_safe(resolved)

    # Open the file, then verify it's the same inode we resolved — this
    # prevents TOCTOU (the file at `resolved` could have been swapped
    # between our validation and open). After opening, the fd pins the
    # inode so no further race exists.
    try:
        fd = os.open(str(resolved), os.O_RDONLY)
    except FileNotFoundError:
        raise FileNotFoundError(f"Local image file does not exist: {source}")
    except OSError as e:
        raise ValueError(f"Cannot open file: {e}")

    try:
        opened_stat = os.fstat(fd)
        resolved_stat = os.stat(resolved)
        if (opened_stat.st_dev, opened_stat.st_ino) != (resolved_stat.st_dev, resolved_stat.st_ino):
            raise ValueError(f"File changed after validation (possible symlink swap): {resolved}")
        _validate_path_safe(Path(os.path.realpath(resolved)))

        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"Not a regular file: {resolved}")

        # Check file extension and MIME type
        ext = resolved.suffix.lower()
        if not ext:
            raise ValueError(
                f"Local file has no extension: {source!r}. "
                f"Supported formats: {', '.join(_IMAGE_EXT_MIME)}"
            )
        mime = _IMAGE_EXT_MIME.get(ext)
        if mime is None:
            raise ValueError(f"Unsupported image format: {ext}. Supported: {', '.join(_IMAGE_EXT_MIME)}")

        file_size = os.fstat(fd).st_size
        if file_size > _MAX_IMAGE_SIZE:
            raise ValueError(
                f"Image file too large: {file_size / 1024 / 1024:.1f} MB. "
                f"MiMo API limit is {_MAX_IMAGE_SIZE / 1024 / 1024:.0f} MB."
            )
        image_data = os.read(fd, file_size)
    finally:
        os.close(fd)

    encoded = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


class MimoVisionProvider:
    """Image understanding provider backed by 小米 MiMo vision model.

    Uses the OpenAI-compatible chat completions endpoint with image support.
    Requires MIMO_API_KEY environment variable.
    """

    def __init__(self) -> None:
        self._http_client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    def close(self) -> None:
        """Close the underlying HTTP client and release pooled connections."""
        with self._client_lock:
            if self._http_client is not None:
                self._http_client.close()
                self._http_client = None

    def _get_client(self) -> httpx.Client:
        """Return (or lazily create) the shared HTTP client with connection pooling."""
        with self._client_lock:
            if self._http_client is None:
                self._http_client = httpx.Client(timeout=120)
            return self._http_client

    def _api_url(self) -> str:
        base = os.environ.get("MIMO_BASE_URL") or _MIMO_DEFAULT_BASE_URL
        return base.rstrip("/") + "/chat/completions"

    def _model(self, **kwargs) -> str:
        return kwargs.get("model") or os.environ.get("MIMO_MODEL") or DEFAULT_MODEL

    def _api_key(self) -> str | None:
        return os.environ.get("MIMO_API_KEY")

    def understand(self, image_source: str, prompt: str = DEFAULT_PROMPT, **kwargs) -> str:
        """Analyze an image and return understanding.

        Args:
            image_source: Image URL (http/https), data URI (data:image/...;base64,...),
                          or local file path. Local paths may be prefixed with ``@``
                          (e.g. ``@screenshot.png`` or ``@/path/to/image.jpg``).
            prompt: Question or instruction about the image.
            **kwargs: Additional options including 'model'.

        Returns:
            JSON string with image_url, prompt, understanding, and model.
        """
        # response_source: used for display in response (strip @ prefix only)
        # processed_url: actual URL/data URI sent to the API
        response_source = image_source.lstrip("@")

        api_key = self._api_key()
        if not api_key:
            return json.dumps({
                "image_url": response_source,
                "prompt": prompt,
                "understanding": "",
                "error": "MIMO_API_KEY not set",
            }, ensure_ascii=False)
        try:
            processed_url = process_image_source(image_source)
        except (FileNotFoundError, ValueError, OSError) as e:
            return json.dumps({
                "image_url": response_source,
                "prompt": prompt,
                "understanding": "",
                "error": f"Image source error: {e}",
            }, ensure_ascii=False)

        model = self._model(**kwargs)

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": processed_url},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_completion_tokens": kwargs.get("max_tokens", 2048),
        }

        try:
            client = self._get_client()
            resp = client.post(
                self._api_url(),
                headers={
                    "api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._format(response_source, prompt, model, data)

        except httpx.HTTPStatusError as e:
            print(f"Error: MiMo HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({
                "image_url": response_source,
                "prompt": prompt,
                "understanding": "",
                "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}",
            }, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: MiMo request failed: {e}", file=sys.stderr)
            return json.dumps({
                "image_url": response_source,
                "prompt": prompt,
                "understanding": "",
                "error": f"Request failed: {e}",
            }, ensure_ascii=False)
        except Exception as e:
            print(f"Error: MiMo vision failed: {e}", file=sys.stderr)
            return json.dumps({
                "image_url": response_source,
                "prompt": prompt,
                "understanding": "",
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False)

    def _format(self, image_source: str, prompt: str, model: str, data: dict) -> str:
        """Extract the text response from MiMo API result."""
        understanding = ""
        reasoning_content = ""

        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            understanding = (message.get("content") or "").strip()
            reasoning = message.get("reasoning_content")
            if reasoning:
                reasoning_content = reasoning.strip()

        result = {
            "image_url": image_source,
            "prompt": prompt,
            "understanding": understanding,
            "model": model,
        }
        if reasoning_content:
            result["reasoning_content"] = reasoning_content

        return json.dumps(result, ensure_ascii=False)
