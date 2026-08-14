"""Windows-only bootstrap for a project accessed through a WSL UNC path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PYWIN32_DLL_DIRECTORY: object | None = None


def enable_pywin32_dll_search_path() -> None:
    """Make pywin32 DLLs visible before importing MCP's Windows stdio code."""

    global _PYWIN32_DLL_DIRECTORY
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return

    dll_directory = Path(sys.prefix) / "Lib" / "site-packages" / "pywin32_system32"
    if dll_directory.is_dir():
        # The returned handle must remain alive while pywin32 is in use.
        _PYWIN32_DLL_DIRECTORY = os.add_dll_directory(str(dll_directory))


def main() -> None:
    enable_pywin32_dll_search_path()
    from server.main import main as serve

    serve()


if __name__ == "__main__":
    main()
