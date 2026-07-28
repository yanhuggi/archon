"""Provider registry for image understanding backends.

.. note::
    The ``ImageProvider`` protocol and registry pattern is also
    duplicated in ``web/server/providers/__init__.py``.
    If you change this file, check and update the web copy as well.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

__all__ = [
    "ImageProvider",
    "register",
    "get_provider",
    "list_providers",
]


@runtime_checkable
class ImageProvider(Protocol):
    """Protocol for image understanding backend implementations."""

    def understand(self, image_source: str, prompt: str = "请详细描述这张图片的内容", **kwargs) -> str:
        """Analyze an image and return a formatted result string."""
        ...


_providers: dict[str, ImageProvider] = {}


def register(name: str, provider: ImageProvider) -> None:
    """Register an image provider."""
    _providers[name] = provider


def get_provider(name: str) -> ImageProvider:
    """Get a registered image provider by name."""
    if name not in _providers:
        raise ValueError(
            f"Unknown image provider: {name!r}. "
            f"Available: {list(_providers.keys())}"
        )
    return _providers[name]


def list_providers() -> Sequence[str]:
    """List all registered provider names."""
    return list(_providers.keys())
