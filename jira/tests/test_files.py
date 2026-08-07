"""Tests for controlled Jira output paths."""

import errno
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from server import files
from server.config import JiraConfig
from server.files import OutputPathError, commit_output_file, resolve_output_path


def test_resolve_output_path_accepts_child_and_changes_suffix(tmp_path) -> None:
    config = JiraConfig(output_dir=tmp_path)
    result = resolve_output_path(str(tmp_path / "nested" / "issue.txt"), config, suffix=".docx")
    assert result == tmp_path / "nested" / "issue.docx"


def test_resolve_output_path_rejects_escape(tmp_path) -> None:
    config = JiraConfig(output_dir=tmp_path / "allowed")
    with pytest.raises(OutputPathError, match="outside"):
        resolve_output_path(str(tmp_path / "outside.txt"), config)


def test_resolve_output_path_rejects_existing_file(tmp_path) -> None:
    destination = tmp_path / "existing.txt"
    destination.write_text("keep", encoding="utf-8")
    with pytest.raises(OutputPathError, match="already exists"):
        resolve_output_path(str(destination), JiraConfig(output_dir=tmp_path))


def test_resolve_output_path_allows_explicit_overwrite(tmp_path) -> None:
    destination = tmp_path / "existing.txt"
    destination.write_text("old", encoding="utf-8")
    config = JiraConfig(output_dir=tmp_path, allow_overwrite=True)
    assert resolve_output_path(str(destination), config) == destination


def test_commit_output_file_does_not_replace_racing_destination(tmp_path) -> None:
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    with pytest.raises(OutputPathError, match="already exists"):
        commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert destination.read_text(encoding="utf-8") == "existing"
    assert temporary.read_text(encoding="utf-8") == "new"


def test_commit_output_file_falls_back_without_hard_links(tmp_path) -> None:
    """Mounts that reject hard links still publish the file."""
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new", encoding="utf-8")

    with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
        commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert destination.read_text(encoding="utf-8") == "new"
    assert not temporary.exists()


def test_hard_link_fallback_never_exposes_a_partial_file(tmp_path) -> None:
    """The fallback publishes by rename, so readers never see half a document."""
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    payload = b"A" * 2_000_000
    temporary.write_bytes(payload)

    observed: list[int] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            try:
                observed.append(destination.stat().st_size)
            except OSError:
                pass
            time.sleep(0.0005)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
            commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))
    finally:
        stop.set()
        watcher.join()

    assert destination.read_bytes() == payload
    assert {size for size in observed} <= {len(payload)}
    assert list(tmp_path.glob(".*.claim")) == []


def test_hard_link_fallback_survives_a_crashed_previous_publish(tmp_path) -> None:
    """A publish killed mid-write must not wedge every later export.

    A fixed staging name would still be on disk after the crash and block the
    retry forever, so the staging name has to be unique per attempt.
    """

    destination = tmp_path / "destination"
    config = JiraConfig(output_dir=tmp_path)
    crashed = tmp_path / "crashed"
    crashed.write_text("interrupted", encoding="utf-8")

    # Kill the first publish after it has created its staging file but before it
    # can move that file into place, leaving whatever residue it had created.
    with (
        patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")),
        patch("server.files.shutil.copyfileobj", side_effect=KeyboardInterrupt),
    ):
        with pytest.raises(KeyboardInterrupt):
            commit_output_file(crashed, destination, config)

    retry = tmp_path / "retry"
    retry.write_text("new", encoding="utf-8")
    with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
        commit_output_file(retry, destination, config)

    assert destination.read_text(encoding="utf-8") == "new"


def _age(path: Path, seconds: float) -> None:
    stale = time.time() - seconds
    os.utime(path, (stale, stale))


def test_publish_reclaims_staging_files_orphaned_by_a_killed_process(tmp_path) -> None:
    """Stale staging files are swept; in-flight ones are left alone."""
    destination = tmp_path / "destination"
    orphan = tmp_path / f"{files._STAGING_PREFIX}old{files._STAGING_SUFFIX}"
    orphan.write_text("orphaned", encoding="utf-8")
    _age(orphan, files._STAGING_MAX_AGE_SECONDS + 60)

    # A staging file young enough to belong to a concurrent publish must survive.
    in_flight = tmp_path / f"{files._STAGING_PREFIX}new{files._STAGING_SUFFIX}"
    in_flight.write_text("in flight", encoding="utf-8")

    temporary = tmp_path / "temporary"
    temporary.write_text("new", encoding="utf-8")
    with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
        commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert not orphan.exists()
    assert in_flight.exists()
    assert destination.read_text(encoding="utf-8") == "new"


def test_cleanup_only_touches_files_this_module_created(tmp_path) -> None:
    """The output directory is the user's; the sweep must not delete their files.

    A bare ".*.publish" glob would match unrelated hidden files that merely share
    the suffix, so the sweep is namespaced to this module's own prefix.
    """

    destination = tmp_path / "destination"
    bystanders = [
        tmp_path / ".user-draft.publish",  # same suffix, not ours
        tmp_path / ".publish",
        tmp_path / "notes.publish",
        tmp_path / "keep.docx",
    ]
    for path in bystanders:
        path.write_text("important", encoding="utf-8")
        _age(path, files._STAGING_MAX_AGE_SECONDS * 10)

    temporary = tmp_path / "temporary"
    temporary.write_text("new", encoding="utf-8")
    with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
        commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    for path in bystanders:
        assert path.read_text(encoding="utf-8") == "important", path
    assert destination.read_text(encoding="utf-8") == "new"


def test_cleanup_runs_even_when_hard_links_work(tmp_path) -> None:
    """The sweep belongs to every publish, not only the no-hard-link fallback."""
    orphan = tmp_path / f"{files._STAGING_PREFIX}old{files._STAGING_SUFFIX}"
    orphan.write_text("orphaned", encoding="utf-8")
    _age(orphan, files._STAGING_MAX_AGE_SECONDS + 60)

    temporary = tmp_path / "temporary"
    temporary.write_text("new", encoding="utf-8")
    # No os.link patch: this is the ordinary hard-link path.
    commit_output_file(temporary, tmp_path / "destination", JiraConfig(output_dir=tmp_path))

    assert not orphan.exists()

    # And on the overwrite path, which returns before either publish strategy.
    second = tmp_path / f"{files._STAGING_PREFIX}other{files._STAGING_SUFFIX}"
    second.write_text("orphaned", encoding="utf-8")
    _age(second, files._STAGING_MAX_AGE_SECONDS + 60)
    replacement = tmp_path / "replacement"
    replacement.write_text("newer", encoding="utf-8")
    commit_output_file(
        replacement, tmp_path / "destination", JiraConfig(output_dir=tmp_path, allow_overwrite=True)
    )

    assert not second.exists()


def test_staging_cleanup_failure_does_not_fail_the_export(tmp_path) -> None:
    """Reclaiming disk space is best effort and must never break a publish."""
    destination = tmp_path / "destination"
    temporary = tmp_path / "temporary"
    temporary.write_text("new", encoding="utf-8")

    with (
        patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")),
        patch.object(Path, "glob", side_effect=OSError("permission denied")),
    ):
        commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert destination.read_text(encoding="utf-8") == "new"


def test_concurrent_publishes_produce_exactly_one_winner(tmp_path) -> None:
    """Racing exports to one path: one succeeds, the rest refuse, nothing is mixed."""
    destination = tmp_path / "destination"
    outcomes: list[str] = []
    lock = threading.Lock()

    def publish(index: int) -> None:
        temporary = tmp_path / f"temporary-{index}"
        temporary.write_text(f"writer-{index}", encoding="utf-8")
        try:
            with patch("server.files.os.link", side_effect=OSError(95, "not supported")):
                commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))
            result = f"{index}:ok"
        except OutputPathError:
            result = f"{index}:refused"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=publish, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [item for item in outcomes if item.endswith(":ok")]
    assert len(winners) == 1
    assert destination.read_text(encoding="utf-8") == f"writer-{winners[0].split(':')[0]}"
    assert list(tmp_path.glob(".*.publish")) == []


@pytest.mark.skipif(
    files._RENAME_NOREPLACE_IMPL is None, reason="renameat2 is unavailable on this platform"
)
def test_atomic_no_replace_rejects_an_existing_destination(tmp_path) -> None:
    """renameat2(RENAME_NOREPLACE) closes the check-then-rename window."""
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    with pytest.raises(OSError) as caught:
        files._RENAME_NOREPLACE_IMPL(source, destination)

    assert caught.value.errno == errno.EEXIST
    assert destination.read_text(encoding="utf-8") == "existing"


def test_fallback_rename_path_still_refuses_to_overwrite(tmp_path) -> None:
    """Platforms without renameat2 keep the no-overwrite guarantee."""
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    with (
        patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")),
        patch("server.files._RENAME_NOREPLACE_IMPL", None),
    ):
        with pytest.raises(OutputPathError, match="already exists"):
            commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert destination.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.glob(".*.publish")) == []


def test_hard_link_fallback_still_refuses_to_overwrite(tmp_path) -> None:
    """The fallback must not weaken the no-overwrite guarantee."""
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    with patch("server.files.os.link", side_effect=OSError(95, "Operation not supported")):
        with pytest.raises(OutputPathError, match="already exists"):
            commit_output_file(temporary, destination, JiraConfig(output_dir=tmp_path))

    assert destination.read_text(encoding="utf-8") == "existing"
    assert temporary.read_text(encoding="utf-8") == "new"


def test_commit_output_file_replaces_when_enabled(tmp_path) -> None:
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new", encoding="utf-8")
    destination.write_text("existing", encoding="utf-8")

    commit_output_file(
        temporary,
        destination,
        JiraConfig(output_dir=tmp_path, allow_overwrite=True),
    )

    assert destination.read_text(encoding="utf-8") == "new"
    assert not temporary.exists()
