"""Tests for server.main — MCP server entry point.

Tests cover:
- .env file loading order and override=True
- Conditional tool registration based on MIMO_API_KEY
- atexit cleanup registration
- FastMCP server creation
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Import once so the module exists for subsequent reload() calls.
# Module-level code (load_dotenv, register_provider, etc.) runs on this
# first import with real dependencies; each test re-triggers it under mocks.
import server.main  # noqa: E402


def _reload_main() -> None:
    """Re-execute server.main module-level code so mocks take effect."""
    importlib.reload(server.main)


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


class TestDotEnv:
    """Tests for .env file loading behavior."""

    def test_load_dotenv_when_env_exists(self) -> None:
        """load_dotenv is called when at least one .env file exists."""
        with patch("dotenv.load_dotenv") as mock_load:
            with patch("pathlib.Path.exists", return_value=True):
                _reload_main()
                mock_load.assert_called_once()

    def test_skip_dotenv_when_no_env_exists(self) -> None:
        """load_dotenv is NOT called when no .env file is found."""
        with patch("dotenv.load_dotenv") as mock_load:
            with patch("pathlib.Path.exists", return_value=False):
                _reload_main()
                mock_load.assert_not_called()

    def test_load_dotenv_override_true(self) -> None:
        """load_dotenv is called with override=True."""
        with patch("dotenv.load_dotenv") as mock_load:
            with patch("pathlib.Path.exists", return_value=True):
                _reload_main()
                mock_load.assert_called_once()
                _args, kwargs = mock_load.call_args
                assert kwargs.get("override") is True

    def test_dotenv_breaks_after_first_found(self) -> None:
        """Only the first existing .env path is loaded; no fallback occurs."""
        with patch("dotenv.load_dotenv") as mock_load:
            # First path exists → loaded immediately → break
            with patch("pathlib.Path.exists", side_effect=[True, False, False]):
                _reload_main()
                mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Conditional tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Tests for conditional analyze_image tool registration."""

    def test_registers_tool_when_key_set(self) -> None:
        """register_analyze_image is called when MIMO_API_KEY is present."""
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register"):
                with patch("server.providers.mimo.MimoVisionProvider"):
                    with patch("server.providers.register"):
                        with patch(
                            "server.tools.analyze_image.register"
                        ) as mock_reg_tool:
                            with patch("mcp.server.fastmcp.FastMCP"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    with patch.dict(
                                        "os.environ",
                                        {"MIMO_API_KEY": "test-key-123"},
                                    ):
                                        _reload_main()
                                        mock_reg_tool.assert_called_once()

    def test_skips_tool_when_key_missing(self) -> None:
        """register_analyze_image is NOT called when MIMO_API_KEY is absent."""
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register"):
                with patch("server.providers.mimo.MimoVisionProvider"):
                    with patch("server.providers.register"):
                        with patch(
                            "server.tools.analyze_image.register"
                        ) as mock_reg_tool:
                            with patch("mcp.server.fastmcp.FastMCP"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    # No MIMO_API_KEY in environ
                                    _reload_main()
                                    mock_reg_tool.assert_not_called()

    def test_warning_printed_when_key_missing(self, capsys) -> None:
        """Warning message is printed to stderr when MIMO_API_KEY is not set."""
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register"):
                with patch("server.providers.mimo.MimoVisionProvider"):
                    with patch("server.providers.register"):
                        with patch("server.tools.analyze_image.register"):
                            with patch("mcp.server.fastmcp.FastMCP"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    _reload_main()
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "MIMO_API_KEY not set" in captured.err


# ---------------------------------------------------------------------------
# atexit cleanup
# ---------------------------------------------------------------------------


class TestAtexit:
    """Tests for atexit cleanup registration."""

    def test_atexit_register_called(self) -> None:
        """atexit.register is called during module initialization."""
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register") as mock_atexit:
                with patch("server.providers.mimo.MimoVisionProvider"):
                    with patch("server.providers.register"):
                        with patch("server.tools.analyze_image.register"):
                            with patch("mcp.server.fastmcp.FastMCP"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    _reload_main()
                                    mock_atexit.assert_called_once()
                                    args, _ = mock_atexit.call_args
                                    assert callable(args[0])

    def test_atexit_register_with_provider_close(self) -> None:
        """atexit.register is called with the provider instance's close method."""
        mock_provider = MagicMock()
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register") as mock_atexit:
                with patch(
                    "server.providers.mimo.MimoVisionProvider",
                    return_value=mock_provider,
                ):
                    with patch("server.providers.register"):
                        with patch("server.tools.analyze_image.register"):
                            with patch("mcp.server.fastmcp.FastMCP"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    _reload_main()
                                    mock_atexit.assert_called_once_with(
                                        mock_provider.close
                                    )


# ---------------------------------------------------------------------------
# FastMCP server creation
# ---------------------------------------------------------------------------


class TestFastMCP:
    """Tests for FastMCP server instantiation."""

    def test_creates_fastmcp_with_correct_name(self) -> None:
        """FastMCP is instantiated with the name 'archon-vision'."""
        with patch("dotenv.load_dotenv"):
            with patch("atexit.register"):
                with patch("server.providers.mimo.MimoVisionProvider"):
                    with patch("server.providers.register"):
                        with patch("server.tools.analyze_image.register"):
                            with patch(
                                "mcp.server.fastmcp.FastMCP"
                            ) as mock_fastmcp:
                                with patch(
                                    "pathlib.Path.exists", return_value=False
                                ):
                                    _reload_main()
                                    mock_fastmcp.assert_called_once_with(
                                        "archon-vision"
                                    )
