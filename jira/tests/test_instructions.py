"""Tests for Jira model-facing instructions."""

from server.instructions import (
    GET_ATTACHMENT_DESCRIPTION,
    ADD_COMMENT_DESCRIPTION,
    DELETE_COMMENT_DESCRIPTION,
    GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION,
    GET_TRANSITIONS_DESCRIPTION,
    SEARCH_JQL_FIELDS_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    TRANSITION_ISSUE_DESCRIPTION,
    UPDATE_COMMENT_DESCRIPTION,
    UPDATE_ISSUE_DESCRIPTION,
)


def test_server_instructions_distinguish_read_and_write_workflows() -> None:
    assert "get_comments" in SERVER_INSTRUCTIONS
    assert "writes a local file" in SERVER_INSTRUCTIONS
    assert "only be called" in SERVER_INSTRUCTIONS
    assert "Never invent" in SERVER_INSTRUCTIONS
    assert "untrusted data" in SERVER_INSTRUCTIONS
    assert "search_jql_fields" in SERVER_INSTRUCTIONS
    assert "get_jql_value_suggestions" in SERVER_INSTRUCTIONS
    assert "update_issue" in SERVER_INSTRUCTIONS
    assert "explicitly asks" in SERVER_INSTRUCTIONS
    assert "add_comment" in SERVER_INSTRUCTIONS
    assert "delete_comment" in SERVER_INSTRUCTIONS
    assert "get_transitions" in SERVER_INSTRUCTIONS
    assert "transition_issue" in SERVER_INSTRUCTIONS


def test_jql_metadata_descriptions_discourage_unnecessary_refreshes() -> None:
    assert "instead of guessing" in SEARCH_JQL_FIELDS_DESCRIPTION
    assert "refresh=true" in SEARCH_JQL_FIELDS_DESCRIPTION
    assert "jql_literal" in GET_JQL_VALUE_SUGGESTIONS_DESCRIPTION


def test_attachment_description_discloses_safety_limits() -> None:
    assert "without writing a local file" in GET_ATTACHMENT_DESCRIPTION
    assert "size" in GET_ATTACHMENT_DESCRIPTION
    assert "cross-origin" in GET_ATTACHMENT_DESCRIPTION
    assert "inline MCP image" in GET_ATTACHMENT_DESCRIPTION


def test_update_description_discloses_remote_write_boundary() -> None:
    assert "explicit" in UPDATE_ISSUE_DESCRIPTION
    assert "overwrites remote issue data" in UPDATE_ISSUE_DESCRIPTION
    assert "does not\ntransition" in UPDATE_ISSUE_DESCRIPTION
    assert "never silently drops fields" in UPDATE_ISSUE_DESCRIPTION


def test_comment_descriptions_disclose_write_boundaries() -> None:
    assert "not idempotent" in ADD_COMMENT_DESCRIPTION
    assert "overwrites remote" in UPDATE_COMMENT_DESCRIPTION
    assert "destructive remote" in DELETE_COMMENT_DESCRIPTION


def test_transition_descriptions_require_discovery_and_explicit_write() -> None:
    assert "exact transition IDs" in GET_TRANSITIONS_DESCRIPTION
    assert "explicitly asks" in TRANSITION_ISSUE_DESCRIPTION
    assert "not idempotent" in TRANSITION_ISSUE_DESCRIPTION
