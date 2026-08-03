"""Tests for validated vision configuration."""

from pathlib import Path

from server.config import VisionConfig


def test_config_reads_provider_and_server_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MIMO_API_KEY", " secret ")
    monkeypatch.setenv("MIMO_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("MIMO_MODEL", "vision-model")
    monkeypatch.setenv("MIMO_TIMEOUT", "30")
    monkeypatch.setenv("MIMO_MAX_TOKENS", "4096")
    monkeypatch.setenv("MIMO_MAX_IMAGE_MB", "12")
    monkeypatch.setenv("MIMO_ALLOWED_DIR", str(tmp_path))
    monkeypatch.setenv("ARCHON_VISION_TRANSPORT", "streamable-http")
    monkeypatch.setenv("ARCHON_VISION_PORT", "9000")

    config = VisionConfig.from_env()
    assert config.api_key == "secret"
    assert config.base_url == "https://example.com/v1"
    assert config.model == "vision-model"
    assert config.timeout == 30
    assert config.max_completion_tokens == 4096
    assert config.max_image_size == 12 * 1024 * 1024
    assert config.allowed_dir == tmp_path.resolve()
    assert config.transport == "streamable-http"
    assert config.port == 9000


def test_invalid_values_fall_back_or_clamp(monkeypatch) -> None:
    monkeypatch.setenv("MIMO_BASE_URL", "not-a-url")
    monkeypatch.setenv("MIMO_TIMEOUT", "invalid")
    monkeypatch.setenv("MIMO_MAX_IMAGE_MB", "100")
    monkeypatch.setenv("ARCHON_VISION_TRANSPORT", "invalid")
    config = VisionConfig.from_env()
    assert config.base_url == "https://api.xiaomimimo.com/v1"
    assert config.timeout == 120
    assert config.max_image_size == 50 * 1024 * 1024
    assert config.transport == "stdio"


def test_non_finite_timeout_uses_default(monkeypatch) -> None:
    monkeypatch.setenv("MIMO_TIMEOUT", "nan")
    assert VisionConfig.from_env().timeout == 120


def test_default_allowed_dir_is_unrestricted(monkeypatch) -> None:
    monkeypatch.delenv("MIMO_ALLOWED_DIR", raising=False)
    assert VisionConfig.from_env().allowed_dir is None
