"""Tests for the dependency-free Jira JQL metadata cache."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from server.config import JiraConfig
from server.jql_cache import _MARKER_RECHECK_INTERVAL, JsonJqlCache


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


def test_value_cache_keeps_the_snapshot_it_just_wrote(tmp_path) -> None:
    """Pruning must not delete the fresh entry when mtimes are indistinguishable."""
    cache = JsonJqlCache(config(tmp_path, jql_value_cache_max_entries=2), clock=Clock())
    original_stat = Path.stat
    seen: dict[str, int] = {}

    def skewed_stat(self, **kwargs):
        """Report each new snapshot as the *oldest* file in the directory.

        Timestamps that do not order by write time are what make an
        mtime-only prune delete the entry it just cached.
        """

        status = original_stat(self, **kwargs)
        if self.suffix != ".json":
            return status
        mtime = seen.setdefault(self.name, 1_000_000 - len(seen))
        fields = list(status)
        fields[8] = mtime
        return os.stat_result(fields, {"st_mtime_ns": mtime * 10**9})

    with patch.object(Path, "stat", skewed_stat):
        for query in ("a", "b", "c"):
            cache.get_values("status", query, lambda q=query: {"suggestions": [q]})

    assert len(list(tmp_path.glob("*-values/*.json"))) == 2
    # Read through a second instance so the assertion depends on the file that
    # survived pruning rather than on this process's memory cache.
    fresh_process = JsonJqlCache(config(tmp_path, jql_value_cache_max_entries=2), clock=Clock())
    reused = fresh_process.get_values(
        "status", "c", lambda: pytest.fail("fresh snapshot was pruned")
    )
    assert reused.source == "disk"
    assert reused.data["suggestions"] == ["c"]


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


def test_eviction_does_not_split_an_inflight_load_across_two_locks(tmp_path) -> None:
    """Trimming a snapshot must not let a second caller load the same entry.

    Reclaiming an entry lock while its loader is still running would hand the
    next caller a different lock, producing a duplicate upstream request and
    letting the slower stale response overwrite the newer one.
    """

    cache = JsonJqlCache(
        config(tmp_path, jql_value_cache_max_entries=1, jql_value_refresh_interval=0),
        clock=Clock(),
    )
    cache.get_values("status", "a", lambda: {"suggestions": ["v1"]})

    inflight = threading.Event()
    release = threading.Event()
    concurrent: list[int] = []

    def slow_stale() -> dict:
        inflight.set()
        release.wait(3)
        return {"suggestions": ["stale"]}

    first = threading.Thread(target=lambda: cache.get_values("status", "a", slow_stale))
    first.start()
    assert inflight.wait(1.0)

    # Writing another key trims the value cache while "a" is still loading.
    cache.get_values("status", "b", lambda: {"suggestions": ["b"]})

    def competing() -> dict:
        concurrent.append(1)
        return {"suggestions": ["fresh"]}

    second = threading.Thread(target=lambda: cache.get_values("status", "a", competing))
    second.start()
    time.sleep(0.3)
    assert concurrent == []  # serialized behind the same lock, not run in parallel

    release.set()
    first.join()
    second.join()
    assert cache._entry_locks == {}  # reclaimed once the last holder released


def test_entry_locks_do_not_leak_across_many_queries(tmp_path) -> None:
    """Reference-counted locks are reclaimed instead of growing without bound."""
    cache = JsonJqlCache(config(tmp_path, jql_value_cache_max_entries=500), clock=Clock())
    for index in range(50):
        cache.get_values("status", f"q{index}", lambda i=index: {"suggestions": [str(i)]})

    assert cache._entry_locks == {}


def test_unrelated_entries_do_not_block_each_other(tmp_path) -> None:
    """A slow field refresh must not serialize unrelated value lookups."""
    cache = JsonJqlCache(config(tmp_path))
    started = threading.Event()

    def slow_fields() -> dict:
        started.set()
        time.sleep(0.3)
        return {"fields": []}

    thread = threading.Thread(target=lambda: cache.get_fields(slow_fields))
    thread.start()
    assert started.wait(1.0)

    began = time.perf_counter()
    cache.get_values("status", "open", lambda: {"suggestions": []})
    elapsed = time.perf_counter() - began

    thread.join()
    assert elapsed < 0.2


def test_warm_hits_do_not_reread_the_invalidation_marker(tmp_path) -> None:
    """The cross-process marker is stat-ed, not re-parsed, on every cache hit."""
    cache = JsonJqlCache(config(tmp_path), clock=Clock())
    cache.get_fields(lambda: {"version": 1})

    reads: list[str] = []
    original_open = Path.open

    def counting_open(self, *args, **kwargs):
        reads.append(self.name)
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", counting_open):
        for _ in range(5):
            cache.get_fields(lambda: pytest.fail("memory cache should be used"))

    assert reads == []


def test_marker_is_reparsed_periodically_when_stat_cannot_be_trusted(tmp_path) -> None:
    """A mount that reuses (inode, mtime, size) must not hide an invalidation forever."""
    clock = Clock()
    # A long refresh interval isolates the marker path: age alone never triggers.
    cache = JsonJqlCache(config(tmp_path, jql_field_refresh_interval=10_000), clock=clock)
    marker = cache._invalidation_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"invalidated_at": 0}), encoding="utf-8")

    cache.get_fields(lambda: {"version": 1})
    # The cold load has no snapshot to serve, so it never consults the marker.
    # One warm hit primes the stat fingerprint that the fast path compares against.
    cache.get_fields(lambda: pytest.fail("snapshot should already be cached"))

    # Rewrite the marker in place, keeping stat's answer identical — what an
    # inode-recycling mount with coarse timestamps reports.
    frozen = os.stat_result(list(marker.stat()), {"st_mtime_ns": marker.stat().st_mtime_ns})
    marker.write_text(json.dumps({"invalidated_at": clock.value + 5}), encoding="utf-8")

    with patch.object(Path, "stat", lambda self, **kwargs: frozen):
        cached = cache.get_fields(lambda: pytest.fail("stat fast path should hold"))
        assert cached.data["version"] == 1

        # Past the recheck interval the parse is forced and the change lands.
        clock.value += _MARKER_RECHECK_INTERVAL + 1
        refreshed = cache.get_fields(lambda: {"version": 2})

    assert refreshed.data["version"] == 2


def test_marker_written_by_another_process_is_still_observed(tmp_path) -> None:
    """Caching the marker's fingerprint must not hide a real invalidation."""
    clock = Clock()
    first = JsonJqlCache(config(tmp_path), clock=clock)
    second = JsonJqlCache(config(tmp_path), clock=clock)
    first.get_fields(lambda: {"version": 1})
    assert first.get_fields(lambda: pytest.fail("should be fresh")).source == "memory"

    clock.value += 1
    second.invalidate()

    assert first.get_fields(lambda: {"version": 2}).data["version"] == 2


def test_concurrent_loads_share_one_inflight_snapshot(tmp_path) -> None:
    cache = JsonJqlCache(config(tmp_path))
    calls = 0
    calls_lock = threading.Lock()

    def loader() -> dict:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return {"version": 1}

    threads = [threading.Thread(target=lambda: cache.get_fields(loader)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
