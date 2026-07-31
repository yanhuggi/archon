"""Tests for model-facing server and tool instructions."""

from server.instructions import SERVER_INSTRUCTIONS, WEB_SEARCH_DESCRIPTION


def test_server_instructions_cover_search_boundaries_and_sources() -> None:
    assert "Do not search" in SERVER_INSTRUCTIONS
    assert "retry once" in SERVER_INSTRUCTIONS
    assert "source URLs" in SERVER_INSTRUCTIONS
    assert "snippets as leads" in SERVER_INSTRUCTIONS


def test_tool_description_explains_inputs_and_error_contract() -> None:
    assert "3-8 important words" in WEB_SEARCH_DESCRIPTION
    assert "time_range" in WEB_SEARCH_DESCRIPTION
    assert "error_code" in WEB_SEARCH_DESCRIPTION
