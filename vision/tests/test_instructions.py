"""Tests for model-facing vision instructions."""

from server.instructions import ANALYZE_IMAGE_DESCRIPTION, SERVER_INSTRUCTIONS


def test_server_instructions_cover_visual_boundaries_and_safety() -> None:
    assert "depends on pixels" in SERVER_INSTRUCTIONS
    assert "Do not infer" in SERVER_INSTRUCTIONS
    assert "uploaded" in SERVER_INSTRUCTIONS
    assert "uncertainty" in SERVER_INSTRUCTIONS


def test_tool_description_supports_discovery_and_errors() -> None:
    assert "screenshots" in ANALYZE_IMAGE_DESCRIPTION
    assert "OCR" in ANALYZE_IMAGE_DESCRIPTION
    assert "local" in ANALYZE_IMAGE_DESCRIPTION
    assert "error_code" in ANALYZE_IMAGE_DESCRIPTION
