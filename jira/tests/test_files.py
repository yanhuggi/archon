"""Tests for controlled Jira output paths."""

import pytest

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
