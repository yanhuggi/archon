"""Tests for server.providers module — Provider registry."""

import pytest

from server.providers import _providers, get_provider, list_providers, register


class _DummyProvider:
    """Dummy provider for testing the registry."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> str:
        return "dummy"


def test_register_and_get_provider() -> None:
    """Register a provider and retrieve it by name."""
    provider = _DummyProvider()
    register("dummy", provider)
    assert get_provider("dummy") is provider


def test_get_provider_unknown() -> None:
    """Getting an unknown provider raises ValueError."""
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nonexistent")


def test_get_provider_unknown_message_includes_available() -> None:
    """Error message lists registered providers."""
    register("foo", _DummyProvider())
    with pytest.raises(ValueError) as excinfo:
        get_provider("bar")
    msg = str(excinfo.value)
    assert "foo" in msg


def test_register_overwrites_existing() -> None:
    """Re-registering with the same name overwrites the old provider."""
    a = _DummyProvider()
    b = _DummyProvider()
    register("x", a)
    register("x", b)
    assert get_provider("x") is b


def test_list_providers_empty() -> None:
    """list_providers returns an empty list when no providers are registered."""
    assert list_providers() == []


def test_list_providers() -> None:
    """list_providers returns all registered provider names."""
    register("a", _DummyProvider())
    register("b", _DummyProvider())
    names = list_providers()
    assert sorted(names) == ["a", "b"]


def test_list_providers_returns_copy() -> None:
    """list_providers returns a new list, not the internal dict keys view."""
    register("x", _DummyProvider())
    result = list_providers()
    result.append("y")
    # Internal registry should still only have "x"
    assert list_providers() == ["x"]


def test_registry_is_isolated(clear_env) -> None:  # noqa: ARG001
    """Each test starts with an isolated registry (conftest clears it)."""
    # _providers is cleared by the autouse fixture
    assert _providers == {}


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("tavily", "server.providers.tavily.TavilyProvider"),
        ("duckduckgo", "server.providers.duckduckgo.DuckDuckGoProvider"),
        ("deepseek", "server.providers.deepseek.DeepSeekProvider"),
    ],
)
def test_real_provider_registration(name: str, cls: str) -> None:
    """Verify that known providers satisfy the SearchProvider protocol."""
    from server.providers import SearchProvider

    # Import and resolve the class
    mod_path, _, class_name = cls.rpartition(".")
    import importlib
    mod = importlib.import_module(mod_path)
    provider_cls = getattr(mod, class_name)
    instance = provider_cls()
    assert isinstance(instance, SearchProvider), (
        f"{cls} does not satisfy the SearchProvider protocol"
    )
