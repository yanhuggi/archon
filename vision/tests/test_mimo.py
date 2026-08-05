"""Tests for server.providers.mimo — MiMo vision provider."""

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from server.config import VisionConfig
from server.providers.mimo import MimoVisionProvider, process_image_source

# The production size ceiling comes from configuration, not a module constant.
_MAX_IMAGE_SIZE = VisionConfig().max_image_size


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> MimoVisionProvider:
    """Return a fresh provider instance."""
    return MimoVisionProvider()


# ---------------------------------------------------------------------------
# process_image_source
# ---------------------------------------------------------------------------


def test_process_url_passthrough() -> None:
    """HTTP/HTTPS URLs pass through unchanged."""
    assert process_image_source("https://example.com/img.jpg") == "https://example.com/img.jpg"
    assert process_image_source("http://pic.com/photo.png") == "http://pic.com/photo.png"


def test_process_rejects_unsupported_url_scheme() -> None:
    with pytest.raises(ValueError, match="http or https"):
        process_image_source("ftp://example.com/image.png")


def test_process_strips_at_prefix_url() -> None:
    """@ prefix is stripped from URLs."""
    assert process_image_source("@https://example.com/img.jpg") == "https://example.com/img.jpg"


def test_process_data_uri_passthrough() -> None:
    """Base64 data URIs pass through unchanged."""
    uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAABJRU5ErkJggg=="
    assert process_image_source(uri) == uri


def test_process_data_uri_rejects_non_image_and_bad_base64() -> None:
    with pytest.raises(ValueError, match="only base64 JPEG and PNG"):
        process_image_source("data:text/plain;base64,aGVsbG8=")
    with pytest.raises(ValueError, match="only base64 JPEG and PNG"):
        process_image_source("data:image/png;base64,not-valid!")


def test_process_data_uri_rejects_mismatched_signature() -> None:
    with pytest.raises(ValueError, match="does not match PNG"):
        process_image_source("data:image/png;base64,aGVsbG8=")


def test_process_strips_at_prefix_data_uri() -> None:
    """@ prefix is stripped from data URIs."""
    uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAABJRU5ErkJggg=="
    assert process_image_source(f"@{uri}") == uri


def test_process_local_file_jpg(tmp_path: Path) -> None:
    """Local .jpg file is read and converted to base64 data URI."""
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal valid JPEG header

    result = process_image_source(str(img))
    assert result.startswith("data:image/jpeg;base64,")


def test_process_local_file_png(tmp_path: Path) -> None:
    """Local .png file is read and converted to base64 data URI."""
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    result = process_image_source(str(img))
    assert result.startswith("data:image/png;base64,")


def test_process_local_file_jpeg_ext(tmp_path: Path) -> None:
    """Local .jpeg file (four-letter extension) is supported."""
    img = tmp_path / "photo.jpeg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    result = process_image_source(str(img))
    assert result.startswith("data:image/jpeg;base64,")


def test_process_local_file_with_at_prefix(tmp_path: Path) -> None:
    """@ prefix is stripped before resolving local file path."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    result = process_image_source(f"@{img}")
    assert result.startswith("data:image/jpeg;base64,")


def test_process_local_file_not_found() -> None:
    """Non-existent local file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="does not exist"):
        process_image_source("/nonexistent/path/image.jpg")


def test_process_local_file_unsupported_format(tmp_path: Path) -> None:
    """Unsupported file extension raises ValueError."""
    img = tmp_path / "test.gif"
    img.write_bytes(b"GIF89a" + b"\x00" * 100)

    with pytest.raises(ValueError, match="Unsupported image format"):
        process_image_source(str(img))


def test_process_local_file_exceeds_size_limit(tmp_path: Path) -> None:
    """File larger than 50 MB raises ValueError."""
    img = tmp_path / "large.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")

    mock_stat = MagicMock(st_size=_MAX_IMAGE_SIZE + 1, st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=1)
    with (
        patch("server.providers.mimo.os.fstat", return_value=mock_stat),
        patch("server.providers.mimo.os.stat", return_value=mock_stat),
    ):
        with pytest.raises(ValueError, match="too large"):
            process_image_source(str(img))


def test_process_local_file_under_size_limit(tmp_path: Path) -> None:
    """File under the limit is accepted and converted."""
    img = tmp_path / "ok.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    result = process_image_source(str(img))
    assert result.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# API Key handling
# ---------------------------------------------------------------------------

def test_missing_api_key(provider: MimoVisionProvider) -> None:
    """understand returns error JSON when MIMO_API_KEY is not set."""
    result = provider.understand("https://example.com/img.jpg")
    data = json.loads(result)
    assert data["error"] == "MIMO_API_KEY is not configured"
    assert data["error_code"] == "configuration_error"
    assert data["understanding"] == ""


def test_missing_api_key_custom_prompt(provider: MimoVisionProvider) -> None:
    """Error response preserves the prompt."""
    result = provider.understand("https://example.com/img.jpg", prompt="图中有什么？")
    data = json.loads(result)
    assert data["prompt"] == "图中有什么？"
    assert data["error"] == "MIMO_API_KEY is not configured"


# ---------------------------------------------------------------------------
# Successful understanding
# ---------------------------------------------------------------------------

def test_successful_understanding(provider: MimoVisionProvider,
                                  mimo_api_response: dict) -> None:
    """understand returns parsed content on success."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            result = provider.understand("https://example.com/sunset.jpg")
            data = json.loads(result)

    assert data["image_url"] == "https://example.com/sunset.jpg"
    assert "日落" in data["understanding"]
    assert data["model"] == "mimo-v2.5"
    assert "error" not in data


def test_successful_understanding_with_reasoning(
    provider: MimoVisionProvider,
    mimo_api_response_with_reasoning: dict,
) -> None:
    """Provider reasoning is not exposed in the public result."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response_with_reasoning

            result = provider.understand("https://example.com/sunset.jpg")
            data = json.loads(result)

    assert "reasoning_content" not in data
    assert "日落" in data["understanding"]


def test_custom_prompt(provider: MimoVisionProvider,
                       mimo_api_response: dict) -> None:
    """understand forwards custom prompt to API."""
    # Modify response to reflect custom prompt context
    response = dict(mimo_api_response)
    response["choices"][0]["message"]["content"] = "图中有一只猫"

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = response

            result = provider.understand(
                "https://example.com/cat.jpg",
                prompt="图中有什么动物？",
            )
            data = json.loads(result)

    assert data["prompt"] == "图中有什么动物？"
    assert "猫" in data["understanding"]

    # Verify prompt was sent in the API request
    call_body = mock_client.post.call_args[1]["json"]
    user_content = call_body["messages"][0]["content"]
    text_part = user_content[1]  # second element is text
    assert text_part["text"] == "图中有什么动物？"


def test_base64_image(provider: MimoVisionProvider,
                      mimo_api_response: dict) -> None:
    """understand handles data URIs (base64 images)."""
    data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAABJRU5ErkJggg=="

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            result = provider.understand(data_uri)
            data = json.loads(result)

    assert data["image_url"] == "data:image/png;base64,<omitted>"
    assert "日落" in data["understanding"]


def test_local_file_path(provider: MimoVisionProvider,
                          mimo_api_response: dict,
                          tmp_path: Path) -> None:
    """understand reads local file and sends base64 to API."""
    img = tmp_path / "sunset.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            result = provider.understand(str(img))
            data = json.loads(result)

    assert data["image_url"] == str(img)
    assert "日落" in data["understanding"]

    # Verify API was called with base64 data URI, not file path
    body = mock_client.post.call_args[1]["json"]
    sent_url = body["messages"][0]["content"][0]["image_url"]["url"]
    assert sent_url.startswith("data:image/png;base64,")


def test_local_file_path_with_at_prefix(provider: MimoVisionProvider,
                                         mimo_api_response: dict,
                                         tmp_path: Path) -> None:
    """understand handles @-prefixed local file path."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            result = provider.understand(f"@{img}")
            data = json.loads(result)

    assert data["image_url"] == str(img)
    assert "日落" in data["understanding"]

    # Verify API received base64
    body = mock_client.post.call_args[1]["json"]
    sent_url = body["messages"][0]["content"][0]["image_url"]["url"]
    assert sent_url.startswith("data:image/jpeg;base64,")


def test_missing_local_file(provider: MimoVisionProvider) -> None:
    """understand returns error for non-existent local file."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        result = provider.understand("/nonexistent/img.jpg")
        data = json.loads(result)

    assert "Image source error" in data["error"]
    assert data["understanding"] == ""


def test_oversized_local_file(provider: MimoVisionProvider,
                               tmp_path: Path) -> None:
    """understand returns error when local file exceeds 50 MB limit."""
    img = tmp_path / "huge.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        mock_stat = MagicMock(st_size=_MAX_IMAGE_SIZE + 1, st_mode=stat.S_IFREG | 0o644, st_dev=1, st_ino=1)
        with (
            patch("server.providers.mimo.os.fstat", return_value=mock_stat),
            patch("server.providers.mimo.os.stat", return_value=mock_stat),
        ):
            result = provider.understand(str(img))
            data = json.loads(result)

    assert "too large" in data["error"]
    assert data["understanding"] == ""


# ---------------------------------------------------------------------------
# API request verification
# ---------------------------------------------------------------------------

def test_sends_correct_request(provider: MimoVisionProvider,
                                mimo_api_response: dict) -> None:
    """verify the request body and headers sent to MiMo API."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "my-secret-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://pic.com/photo.jpg", prompt="描述图片")

            call_kwargs = mock_client.post.call_args[1]
            req_url = mock_client.post.call_args[0][0]

    # Endpoint
    assert req_url == "https://api.xiaomimimo.com/v1/chat/completions"

    # Auth header
    assert call_kwargs["headers"]["api-key"] == "my-secret-key"
    assert call_kwargs["headers"]["Content-Type"] == "application/json"

    # Request body
    body = call_kwargs["json"]
    assert body["model"] == "mimo-v2.5"
    assert body["max_completion_tokens"] == 2048

    user_msg = body["messages"][0]
    assert user_msg["role"] == "user"

    content = user_msg["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "https://pic.com/photo.jpg"
    assert content[1] == {"type": "text", "text": "描述图片"}


# ---------------------------------------------------------------------------
# Configuration overrides
# ---------------------------------------------------------------------------

def test_custom_api_url(provider: MimoVisionProvider,
                        mimo_api_response: dict) -> None:
    """MIMO_BASE_URL overrides the default endpoint."""
    custom_base = "https://custom-mimo.example.com/v1"
    expected_url = f"{custom_base}/chat/completions"

    with patch.dict("os.environ", {
        "MIMO_API_KEY": "test-key",
        "MIMO_BASE_URL": custom_base,
    }):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://example.com/img.jpg")

            req_url = mock_client.post.call_args[0][0]
    assert req_url == expected_url


def test_custom_model_env(provider: MimoVisionProvider,
                          mimo_api_response: dict) -> None:
    """MIMO_MODEL env var changes the model name."""
    with patch.dict("os.environ", {
        "MIMO_API_KEY": "test-key",
        "MIMO_MODEL": "mimo-v3.0",
    }):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://example.com/img.jpg")

            body = mock_client.post.call_args[1]["json"]
    assert body["model"] == "mimo-v3.0"


def test_custom_model_kwarg(provider: MimoVisionProvider,
                            mimo_api_response: dict) -> None:
    """model kwarg overrides both default and env var."""
    with patch.dict("os.environ", {
        "MIMO_API_KEY": "test-key",
        "MIMO_MODEL": "mimo-v3.0",
    }):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://example.com/img.jpg", model="mimo-v2.5-pro")

            body = mock_client.post.call_args[1]["json"]
    assert body["model"] == "mimo-v2.5-pro"


def test_custom_max_tokens(provider: MimoVisionProvider,
                           mimo_api_response: dict) -> None:
    """max_tokens kwarg overrides default."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://example.com/img.jpg", max_tokens=4096)

            body = mock_client.post.call_args[1]["json"]
    assert body["max_completion_tokens"] == 4096


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_http_401_error(provider: MimoVisionProvider) -> None:
    """understand handles 401 Unauthorized."""
    mock_request = httpx.Request("POST", MimoVisionProvider()._api_url())
    with patch.dict("os.environ", {"MIMO_API_KEY": "bad-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized",
                request=mock_request,
                response=httpx.Response(401, text='{"error":"unauthorized"}'),
            )

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert "401" in data["error"]
    assert data["understanding"] == ""
    assert data["image_url"] == "https://example.com/img.jpg"
    assert data["error_code"] == "authentication_error"


def test_http_429_error(provider: MimoVisionProvider) -> None:
    """understand handles 429 Rate Limited."""
    mock_request = httpx.Request("POST", MimoVisionProvider()._api_url())
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=mock_request,
                response=httpx.Response(429, text='{"error":"rate_limit_exceeded"}'),
            )

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert "429" in data["error"]
    assert data["error_code"] == "rate_limited"


def test_request_error(provider: MimoVisionProvider) -> None:
    """understand handles network errors."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.side_effect = httpx.RequestError("Connection refused")

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert "Request failed" in data["error"]


def test_empty_response(provider: MimoVisionProvider) -> None:
    """understand handles empty API response gracefully."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = {}

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert data["understanding"] == ""
    assert data["error_code"] == "invalid_provider_response"


def test_non_json_body_is_a_provider_contract_error(provider: MimoVisionProvider) -> None:
    """A 200 response with a non-JSON body maps to invalid_provider_response."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.json.side_effect = ValueError("Expecting value")

            data = json.loads(provider.understand("https://example.com/img.jpg"))

    assert data["error_code"] == "invalid_provider_response"
    assert data["understanding"] == ""
    assert "not valid JSON" in data["error"]


def test_truncated_reply_is_flagged(provider: MimoVisionProvider,
                                    mimo_api_response: dict) -> None:
    """A length-capped reply is marked so callers do not trust it as complete."""
    response = dict(mimo_api_response)
    response["choices"][0]["finish_reason"] = "length"

    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.json.return_value = response

            data = json.loads(provider.understand("https://example.com/img.jpg"))

    assert data["truncated"] is True
    assert data["finish_reason"] == "length"
    assert data["understanding"]


def test_complete_reply_is_not_flagged(provider: MimoVisionProvider,
                                       mimo_api_response: dict) -> None:
    """A normal stop reason adds no truncation keys."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.json.return_value = mimo_api_response

            data = json.loads(provider.understand("https://example.com/img.jpg"))

    assert "truncated" not in data
    assert "finish_reason" not in data


def test_empty_choices(provider: MimoVisionProvider) -> None:
    """understand handles empty choices list."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = {
                "id": "test",
                "choices": [],
                "model": "mimo-v2.5",
            }

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert data["understanding"] == ""
    assert data["error_code"] == "invalid_provider_response"


# ---------------------------------------------------------------------------
# Defaults / invariants
# ---------------------------------------------------------------------------

def test_default_prompt(provider: MimoVisionProvider,
                        mimo_api_response: dict) -> None:
    """Default prompt is Chinese."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.return_value.status_code = 200
            mock_client.post.return_value.json.return_value = mimo_api_response

            provider.understand("https://example.com/img.jpg")

            body = mock_client.post.call_args[1]["json"]
            text_part = body["messages"][0]["content"][1]

    assert text_part["text"] == "请详细描述这张图片的内容"


# ---------------------------------------------------------------------------
# edge cases — process_image_source
# ---------------------------------------------------------------------------


def test_process_local_file_no_extension(tmp_path: Path) -> None:
    """File without an extension raises ValueError with 'no extension'."""
    img = tmp_path / "image"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    with pytest.raises(ValueError, match="no extension"):
        process_image_source(str(img))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs need POSIX")
def test_process_fifo_does_not_block(tmp_path: Path) -> None:
    """A FIFO named like an image is rejected instead of parking the thread."""
    import threading

    fifo = tmp_path / "trap.png"
    os.mkfifo(fifo)

    outcome: dict[str, str] = {}
    finished = threading.Event()

    def call() -> None:
        try:
            process_image_source(str(fifo))
        except BaseException as exc:  # noqa: BLE001 - record whatever surfaces
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finished.set()

    threading.Thread(target=call, daemon=True).start()
    assert finished.wait(timeout=10), "process_image_source blocked on a FIFO"
    assert "Not a regular file" in outcome.get("error", "")


def test_process_data_uri_accepts_wrapped_base64() -> None:
    """Line-wrapped base64, as produced by MIME encoders, is accepted."""
    import base64

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    encoded = base64.b64encode(png).decode("ascii")
    wrapped = "\n".join(encoded[i:i + 20] for i in range(0, len(encoded), 20))

    result = process_image_source(f"data:image/png;base64,{wrapped}")

    assert result == f"data:image/png;base64,{encoded}"


def test_process_local_file_not_regular(tmp_path: Path) -> None:
    """A directory path (not a regular file) raises ValueError."""
    img = tmp_path / "dir.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    # Patch fstat and stat to return a directory mode instead of regular file
    mock_stat = MagicMock(st_size=100, st_mode=stat.S_IFDIR | 0o755, st_dev=2, st_ino=2)
    with (
        patch("server.providers.mimo.os.fstat", return_value=mock_stat),
        patch("server.providers.mimo.os.stat", return_value=mock_stat),
    ):
        with pytest.raises(ValueError, match="Not a regular file"):
            process_image_source(str(img))


def test_path_traversal_rejected(tmp_path: Path) -> None:
    """Path escaping outside the allowed directory is rejected."""
    malicious = tmp_path / ".." / ".." / "etc" / "passwd"
    with patch.dict(os.environ, {"MIMO_ALLOWED_DIR": str(tmp_path)}):
        with pytest.raises(ValueError, match="Access denied"):
            process_image_source(str(malicious))


def test_path_traversal_symlink_rejected(tmp_path: Path) -> None:
    """Symlink pointing outside the allowed directory is rejected."""
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    link = safe_dir / "outside_link.jpg"
    target = tmp_path.parent / "secret.txt"
    target.write_text("secret")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink not supported on this platform")

    with patch.dict(os.environ, {"MIMO_ALLOWED_DIR": str(safe_dir.resolve())}):
        with pytest.raises(ValueError, match="Access denied"):
            process_image_source(str(link))


def test_absolute_path_outside_allowed_rejected() -> None:
    """An absolute path outside the allowed directory is rejected."""
    with patch.dict(os.environ, {"MIMO_ALLOWED_DIR": "/tmp"}):
        with pytest.raises(ValueError, match="Access denied"):
            process_image_source("/etc/passwd")


def test_path_within_allowed_accepted(tmp_path: Path) -> None:
    """A path within the allowed directory is accepted."""
    img = tmp_path / "safe.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    with patch.dict(os.environ, {"MIMO_ALLOWED_DIR": str(tmp_path)}):
        result = process_image_source(str(img))
    assert result.startswith("data:image/jpeg;base64,")


def test_path_is_unrestricted_by_default(tmp_path: Path, monkeypatch) -> None:
    """Local files are accepted without a configured directory boundary."""
    img = tmp_path / "unrestricted.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    monkeypatch.delenv("MIMO_ALLOWED_DIR", raising=False)

    result = process_image_source(str(img))

    assert result.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# unexpected exception in understand
# ---------------------------------------------------------------------------


def test_unexpected_exception(provider: MimoVisionProvider) -> None:
    """A plain Exception from httpx is caught and returned as JSON error."""
    with patch.dict("os.environ", {"MIMO_API_KEY": "test-key"}):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.post.side_effect = Exception("boom")

            result = provider.understand("https://example.com/img.jpg")
            data = json.loads(result)

    assert data["error"] == "Exception: boom"
    assert data["understanding"] == ""


# ---------------------------------------------------------------------------
# client lifecycle
# ---------------------------------------------------------------------------


def test_client_close() -> None:
    """Closing the client sets _http_client to None; next get creates new."""
    provider = MimoVisionProvider()

    # Create the client
    client1 = provider._get_client()
    assert provider._http_client is client1
    assert client1 is not None

    # Close — client is discarded
    provider.close()
    assert provider._http_client is None

    # Next call creates a brand-new client
    client2 = provider._get_client()
    assert provider._http_client is client2
    assert client2 is not client1


# ---------------------------------------------------------------------------
# provider registry — list_providers
# ---------------------------------------------------------------------------


def test_list_providers() -> None:
    """list_providers returns all registered provider names."""
    from server.providers import register as reg, list_providers

    reg("alpha", MagicMock())
    reg("beta", MagicMock())

    names = list_providers()
    assert "alpha" in names
    assert "beta" in names
    assert len(names) == 2


# ---------------------------------------------------------------------------
# ImageProvider protocol — runtime_checkable
# ---------------------------------------------------------------------------


def test_provider_protocol_runtime_checkable() -> None:
    """MimoVisionProvider satisfies the runtime-checkable ImageProvider protocol."""
    from server.providers import ImageProvider

    provider = MimoVisionProvider()
    assert isinstance(provider, ImageProvider)
