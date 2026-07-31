"""Shared fixtures for archon-web tests."""

from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clear_env(tmp_path) -> Generator[None, None, None]:
    """Clear provider env vars before each test.

    This fixture runs automatically for every test,
    ensuring tests start from a clean environment.
    """
    from server.providers import _providers

    _providers.clear()
    with patch.dict(
        "os.environ",
        {"ARCHON_WEB_RATE_LIMIT_FILE": str(tmp_path / "rate-limit")},
        clear=True,
    ):
        yield
    _providers.clear()
