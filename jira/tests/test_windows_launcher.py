"""Tests for the Windows MCP bootstrap."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from server import windows_launcher


def test_windows_bootstrap_registers_pywin32_dll_directory(tmp_path: Path) -> None:
    dll_directory = tmp_path / "Lib" / "site-packages" / "pywin32_system32"
    dll_directory.mkdir(parents=True)
    handle = MagicMock()

    with (
        patch("server.windows_launcher.sys.platform", "win32"),
        patch("server.windows_launcher.sys.prefix", str(tmp_path)),
        patch(
            "server.windows_launcher.os.add_dll_directory",
            return_value=handle,
            create=True,
        ) as add_directory,
    ):
        windows_launcher.enable_pywin32_dll_search_path()

    add_directory.assert_called_once_with(str(dll_directory))
    assert windows_launcher._PYWIN32_DLL_DIRECTORY is handle
