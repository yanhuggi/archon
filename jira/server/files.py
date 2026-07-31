"""Local output-path validation shared by Jira write tools."""

from __future__ import annotations

import os
from pathlib import Path

from server.config import JiraConfig


class OutputPathError(ValueError):
    """Raised when a requested local output path is unsafe."""


def resolve_output_path(
    value: str,
    config: JiraConfig,
    *,
    suffix: str | None = None,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise OutputPathError("save_to must not be empty")
    if "\x00" in value:
        raise OutputPathError("save_to contains a null byte")

    try:
        candidate = Path(value.strip()).expanduser()
        if suffix is not None:
            candidate = candidate.with_suffix(suffix)
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise OutputPathError(f"Invalid output path: {exc}") from exc
    if not resolved.is_relative_to(config.output_dir):
        raise OutputPathError(
            f"Output path '{resolved}' is outside the allowed directory "
            f"'{config.output_dir}'. Set JIRA_ALLOWED_OUTPUT_DIR if needed."
        )
    if resolved.exists():
        if resolved.is_dir():
            raise OutputPathError(f"Output path is a directory: {resolved}")
        if not config.allow_overwrite:
            raise OutputPathError(
                f"Output file already exists: {resolved}. Set JIRA_ALLOW_OVERWRITE=true to replace it."
            )
    return resolved


def commit_output_file(temporary_path: Path, destination: Path, config: JiraConfig) -> None:
    """Publish a completed temporary file without weakening overwrite policy."""

    if config.allow_overwrite:
        os.replace(temporary_path, destination)
        return

    try:
        # Both paths are in the same directory, so a hard link gives us an
        # atomic create-if-absent operation on supported local filesystems.
        os.link(temporary_path, destination)
    except FileExistsError as exc:
        raise OutputPathError(f"Output file already exists: {destination}") from exc
    temporary_path.unlink()
