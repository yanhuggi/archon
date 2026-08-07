"""Local output-path validation shared by Jira write tools."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from server.config import JiraConfig

LOGGER = logging.getLogger(__name__)

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_STAGING_SUFFIX = ".publish"
# Staging files carry an application-specific prefix so the cleanup sweep can
# only ever match files this module created. A bare ".*.publish" glob would also
# match unrelated hidden files that happen to share the suffix.
_STAGING_PREFIX = ".archon-jira-publish-"
_STAGING_GLOB = f"{_STAGING_PREFIX}*{_STAGING_SUFFIX}"
# A staging file older than this cannot belong to a live publish: the writer only
# holds it for one copy, so anything this stale was orphaned by a killed process.
_STAGING_MAX_AGE_SECONDS = 6 * 60 * 60


class OutputPathError(ValueError):
    """Raised when a requested local output path is unsafe."""


def _load_rename_noreplace():
    """Return a ``renameat2(RENAME_NOREPLACE)`` callable, or None if unavailable.

    This is the only primitive that both creates the destination and refuses to
    replace it in a single syscall. It needs Linux 3.15+ and filesystem support,
    so callers must keep a fallback.
    """

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.renameat2
    except (AttributeError, OSError):
        return None
    syscall.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    syscall.restype = ctypes.c_int

    def rename_noreplace(source: Path, destination: Path) -> None:
        ctypes.set_errno(0)
        if syscall(
            _AT_FDCWD,
            os.fsencode(source),
            _AT_FDCWD,
            os.fsencode(destination),
            _RENAME_NOREPLACE,
        ):
            raise OSError(ctypes.get_errno(), "renameat2 failed", str(destination))

    return rename_noreplace


_RENAME_NOREPLACE_IMPL = _load_rename_noreplace()


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


def _discard_abandoned_staging_files(directory: Path) -> None:
    """Remove staging files orphaned by a killed process.

    A publish that is force-killed leaves its uniquely named staging file behind.
    It never blocks a later publish, but it does occupy disk, so each publish
    sweeps siblings that are too old to belong to an in-flight write.

    The glob is restricted to this module's own prefix: the output directory
    belongs to the user, and a broader pattern could delete their files. Failures
    are ignored, since reclaiming space must never fail an export.
    """

    cutoff = time.time() - _STAGING_MAX_AGE_SECONDS
    try:
        candidates = list(directory.glob(_STAGING_GLOB))
    except OSError as exc:
        LOGGER.debug("Could not scan %s for abandoned staging files: %s", directory, exc)
        return
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                LOGGER.info("Removed abandoned Jira export staging file: %s", path)
        except OSError as exc:
            LOGGER.debug("Could not remove abandoned staging file %s: %s", path, exc)


def commit_output_file(temporary_path: Path, destination: Path, config: JiraConfig) -> None:
    """Publish a completed temporary file without weakening overwrite policy."""

    _discard_abandoned_staging_files(destination.parent)

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

    Copying straight into the destination would expose a partially written file,
    so content lands in a uniquely named sibling and is then moved into place.
    The staging name is unique per attempt: a fixed name would survive a crash
    and permanently block later exports.

    ``renameat2(RENAME_NOREPLACE)`` performs the move without replacing an
    existing destination. Where it is unavailable, a plain ``os.rename`` is used
    after an existence check, which leaves a small window in which a racing
    writer's file could be replaced. That is unavoidable without the syscall,
    and it is narrower than the fully non-atomic copy it replaces.
    """

    staging: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=_STAGING_PREFIX, suffix=_STAGING_SUFFIX, dir=destination.parent
        )
        staging = Path(name)
    except OSError as exc:
        raise OutputPathError(f"Could not write output file {destination}: {exc}") from exc

    try:
        with open(descriptor, "wb") as handle, temporary_path.open("rb") as source:
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())

        if _RENAME_NOREPLACE_IMPL is not None:
            try:
                _RENAME_NOREPLACE_IMPL(staging, destination)
                return
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise OutputPathError(f"Output file already exists: {destination}") from exc
                if exc.errno not in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
                    raise
                LOGGER.debug(
                    "renameat2 unsupported for %s; falling back to rename", destination
                )

        if destination.exists():
            raise OutputPathError(f"Output file already exists: {destination}")
        os.rename(staging, destination)
    except OutputPathError:
        staging.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise OutputPathError(f"Could not write output file {destination}: {exc}") from exc
