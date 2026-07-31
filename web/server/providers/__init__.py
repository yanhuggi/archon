"""Search provider registry."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for search backend implementations."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        """Execute a search and return a formatted result string."""
        ...


_providers: dict[str, SearchProvider] = {}


def register(name: str, provider: SearchProvider) -> None:
    """Register a search provider."""
    _providers[name] = provider


def get_provider(name: str) -> SearchProvider:
    """Get a registered provider by name."""
    if name not in _providers:
        raise ValueError(
            f"Unknown provider: {name!r}. "
            f"Available: {list(_providers.keys())}"
        )
    return _providers[name]


def list_providers() -> Sequence[str]:
    """List all registered provider names."""
    return list(_providers.keys())


def is_registered(name: str) -> bool:
    """Check if a provider is registered by name."""
    return name in _providers
