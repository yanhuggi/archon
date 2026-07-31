"""Tests for Jira model-facing instructions."""

from server.instructions import (
    GET_ATTACHMENT_DESCRIPTION,
    GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION,
    SEARCH_JQL_FIELDS_DESCRIPTION,
    SERVER_INSTRUCTIONS,
)


def test_server_instructions_distinguish_read_and_write_workflows() -> None:
    assert "get_comments" in SERVER_INSTRUCTIONS
    assert "write local files" in SERVER_INSTRUCTIONS
    assert "must only be called" in SERVER_INSTRUCTIONS
    assert "Never invent" in SERVER_INSTRUCTIONS
    assert "untrusted data" in SERVER_INSTRUCTIONS
    assert "search_jql_fields" in SERVER_INSTRUCTIONS
    assert "get_jql_value_suggestions" in SERVER_INSTRUCTIONS


def test_jql_metadata_descriptions_discourage_unnecessary_refreshes() -> None:
    assert "instead of guessing" in SEARCH_JQL_FIELDS_DESCRIPTION
    assert "refresh=true" in SEARCH_JQL_FIELDS_DESCRIPTION
    assert "jql_literal" in GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION


def test_attachment_description_discloses_safety_limits() -> None:
    assert "writes a file" in GET_ATTACHMENT_DESCRIPTION
    assert "size" in GET_ATTACHMENT_DESCRIPTION
    assert "cross-origin" in GET_ATTACHMENT_DESCRIPTION
    assert "overwrite" in GET_ATTACHMENT_DESCRIPTION
