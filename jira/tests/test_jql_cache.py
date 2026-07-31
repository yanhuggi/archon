"""Tests for the dependency-free Jira JQL metadata cache."""

import json

import pytest

from server.config import JiraConfig
from server.jql_cache import JsonJqlCache


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def config(tmp_path, **overrides) -> JiraConfig:
    values = {
        "url": "https://jira.example.com",
        "username": "user",
        "password": "pass",
        "jql_cache_dir": tmp_path,
        "jql_field_refresh_interval": 10,
        "jql_value_refresh_interval": 20,
        "jql_cache_max_stale": 100,
    }
    values.update(overrides)
    return JiraConfig(**values)


def test_fields_cache_persists_and_is_reused_by_new_process(tmp_path) -> None:
    clock = Clock()
    first = JsonJqlCache(config(tmp_path), clock=clock)
    loaded = first.get_fields(lambda: {"fields": [{"id": "status"}]})
    assert loaded.source == "jira"

    second = JsonJqlCache(config(tmp_path), clock=clock)
    reused = second.get_fields(lambda: pytest.fail("fresh disk cache should be used"))

    assert reused.source == "disk"
    assert reused.data["fields"][0]["id"] == "status"


def test_expired_cache_refreshes_and_stale_cache_survives_failure(tmp_path) -> None:
    clock = Clock()
    cache = JsonJqlCache(config(tmp_path), clock=clock)
    cache.get_fields(lambda: {"version": 1})
    clock.value += 11
    refreshed = cache.get_fields(lambda: {"version": 2})
    assert refreshed.data["version"] == 2

    clock.value += 11
    stale = cache.get_fields(lambda: (_ for _ in ()).throw(OSError("offline")))
    assert stale.data["version"] == 2
    assert stale.stale is True


def test_cache_does_not_use_snapshot_past_max_stale(tmp_path) -> None:
    clock = Clock()
    cache = JsonJqlCache(config(tmp_path), clock=clock)
    cache.get_fields(lambda: {"version": 1})
    clock.value += 101

    with pytest.raises(OSError, match="offline"):
        cache.get_fields(lambda: (_ for _ in ()).throw(OSError("offline")))


def test_refresh_bypasses_fresh_cache(tmp_path) -> None:
    cache = JsonJqlCache(config(tmp_path), clock=Clock())
    cache.get_fields(lambda: {"version": 1})
    refreshed = cache.get_fields(lambda: {"version": 2}, refresh=True)
    assert refreshed.data["version"] == 2


def test_value_queries_use_independent_atomic_snapshots(tmp_path) -> None:
    cache = JsonJqlCache(config(tmp_path), clock=Clock())
    cache.get_values("status", "open", lambda: {"suggestions": ["Open"]})
    cache.get_values("status", "done", lambda: {"suggestions": ["Done"]})

    files = list(tmp_path.glob("*-values/*.json"))
    assert len(files) == 2
    assert all(json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1 for path in files)
    assert list(tmp_path.rglob("*.tmp")) == []


def test_value_cache_prunes_oldest_entries(tmp_path) -> None:
    clock = Clock()
    cache = JsonJqlCache(config(tmp_path, jql_value_cache_max_entries=1), clock=clock)
    cache.get_values("status", "open", lambda: {"suggestions": ["Open"]})
    clock.value += 1
    cache.get_values("status", "done", lambda: {"suggestions": ["Done"]})

    assert len(list(tmp_path.glob("*-values/*.json"))) == 1
    assert len([key for key in cache._memory if key.startswith("values:")]) == 1


def test_invalidated_snapshot_is_refreshed(tmp_path) -> None:
    clock = Clock()
    first = JsonJqlCache(config(tmp_path), clock=clock)
    first.get_fields(lambda: {"version": 1})
    second = JsonJqlCache(config(tmp_path), clock=clock)
    second.get_fields(lambda: pytest.fail("disk snapshot should be fresh"))
    clock.value += 1
    first.invalidate()

    refreshed = second.get_fields(lambda: {"version": 2})

    assert refreshed.source == "jira"
    assert refreshed.data["version"] == 2


def test_cache_is_isolated_by_jira_user(tmp_path) -> None:
    clock = Clock()
    first = JsonJqlCache(config(tmp_path, username="first"), clock=clock)
    second = JsonJqlCache(config(tmp_path, username="second"), clock=clock)
    first.get_fields(lambda: {"user": "first"})
    result = second.get_fields(lambda: {"user": "second"})

    assert result.data["user"] == "second"
    assert len(list(tmp_path.glob("*-fields.json"))) == 2


def test_unwritable_cache_path_falls_back_to_memory(tmp_path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = JsonJqlCache(config(blocked), clock=Clock())

    first = cache.get_fields(lambda: {"version": 1})
    second = cache.get_fields(lambda: pytest.fail("memory cache should be used"))

    assert first.source == "jira"
    assert second.source == "memory"


def test_corrupt_snapshot_is_ignored(tmp_path) -> None:
    cache = JsonJqlCache(config(tmp_path), clock=Clock())
    cache._fields_path().write_text("not-json", encoding="utf-8")

    result = cache.get_fields(lambda: {"version": 2})

    assert result.source == "jira"
    assert result.data["version"] == 2
