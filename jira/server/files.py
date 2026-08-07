"""Local output-path validation shared by Jira write tools."""

from __future__ import annotations

import os
import shutil
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
    except OSError:
        # Some mounts (network shares, certain FUSE and Windows-backed paths)
        # reject hard links outright.
        _publish_without_hard_link(temporary_path, destination)
    temporary_path.unlink(missing_ok=True)


def _publish_without_hard_link(temporary_path: Path, destination: Path) -> None:
    """Publish atomically on mounts that reject hard links.

    A plain copy into the destination would make a partially written file
    visible to readers, so the content goes to a sibling claim file first and is
    then moved into place with a single ``os.rename``. ``O_EXCL`` on the claim
    keeps the create-if-absent guarantee: whoever creates it owns the publish,
    and the final rename only ever exposes a complete file.
    """

    claim = destination.with_name(f".{destination.name}.claim")
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise OutputPathError(
            f"Another export is already writing {destination}; retry once it finishes."
        ) from exc
    except OSError as exc:
        raise OutputPathError(f"Could not write output file {destination}: {exc}") from exc

    try:
        with open(descriptor, "wb") as handle, temporary_path.open("rb") as source:
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        # Re-check late: without hard links there is no single syscall that both
        # creates the destination and copies into it.
        if destination.exists():
            raise OutputPathError(f"Output file already exists: {destination}")
        os.rename(claim, destination)
    except OutputPathError:
        claim.unlink(missing_ok=True)
        raise
    except OSError as exc:
        claim.unlink(missing_ok=True)
        raise OutputPathError(f"Could not write output file {destination}: {exc}") from exc
