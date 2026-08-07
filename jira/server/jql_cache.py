"""Dependency-free memory and JSON snapshot cache for Jira JQL metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from server.config import JiraConfig

LOGGER = logging.getLogger(__name__)
CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CacheResult:
    data: dict
    fetched_at: float
    source: str
    stale: bool = False

    def metadata(self) -> dict:
        return {
            "source": self.source,
            "fetched_at": datetime.fromtimestamp(self.fetched_at, timezone.utc).isoformat(),
            "stale": self.stale,
        }


class JsonJqlCache:
    """Cache complete snapshots in memory and atomically-written JSON files."""

    def __init__(self, config: JiraConfig, *, clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._clock = clock
        identity = f"{config.url or ''}\0{config.username or ''}".encode()
        self._instance_key = hashlib.sha256(identity).hexdigest()[:20]
        self._memory: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._entry_locks: dict[str, threading.RLock] = {}
        self._marker_fingerprint: tuple[int, int, int] | None = None
        self._marker_value: dict | None = None

    def _entry_lock(self, cache_id: str) -> threading.RLock:
        """Return the per-entry lock that serializes one snapshot's loader.

        Loaders perform Jira HTTP calls, so holding a single global lock across
        them would make an in-flight field refresh block every unrelated value
        lookup. Coalescing stays per cache entry, which is where it matters.
        """

        with self._lock:
            lock = self._entry_locks.get(cache_id)
            if lock is None:
                lock = threading.RLock()
                self._entry_locks[cache_id] = lock
            return lock

    def get_fields(self, loader: Callable[[], dict], *, refresh: bool = False) -> CacheResult:
        with self._entry_lock("fields"):
            return self._get(
                "fields",
                self._fields_path(),
                self._config.jql_field_refresh_interval,
                loader,
                refresh=refresh,
            )

    def get_values(
        self,
        field: str,
        query: str,
        loader: Callable[[], dict],
        *,
        refresh: bool = False,
    ) -> CacheResult:
        cache_id = "values:" + hashlib.sha256(
            json.dumps([field, query], ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        with self._entry_lock(cache_id):
            return self._get(
                cache_id,
                self._values_path(cache_id.removeprefix("values:")),
                self._config.jql_value_refresh_interval,
                loader,
                refresh=refresh,
            )

    def invalidate(self) -> None:
        """Mark snapshots stale across MCP processes after a JQL validation error."""

        with self._lock:
            invalidated_at = self._clock()
            self._memory["__invalidated__"] = {"invalidated_at": invalidated_at}
            self._marker_fingerprint = None
            self._marker_value = None
            if self._config.jql_disk_cache_enabled:
                self._write_json(self._invalidation_path(), {"invalidated_at": invalidated_at})

    def _get(
        self,
        cache_id: str,
        path: Path,
        refresh_interval: int,
        loader: Callable[[], dict],
        *,
        refresh: bool,
    ) -> CacheResult:
        now = self._clock()
        with self._lock:
            snapshot = self._memory.get(cache_id)
        source = "memory"
        if snapshot is None and self._config.jql_disk_cache_enabled:
            snapshot = self._read_snapshot(path)
            source = "disk"
            if snapshot is not None:
                with self._lock:
                    self._memory[cache_id] = snapshot

        if snapshot is not None:
            fetched_at = float(snapshot["fetched_at"])
            age = max(0.0, now - fetched_at)
            # Only consult the cross-process invalidation marker for a snapshot
            # that would otherwise be served, so warm hits stay in memory.
            if not refresh and age < refresh_interval and fetched_at >= self._invalidated_at():
                return CacheResult(snapshot["data"], fetched_at, source)

        try:
            data = loader()
            if not isinstance(data, dict):
                raise TypeError("JQL metadata loader must return an object")
        except Exception:
            if snapshot is not None:
                fetched_at = float(snapshot["fetched_at"])
                if max(0.0, now - fetched_at) <= self._config.jql_cache_max_stale:
                    return CacheResult(snapshot["data"], fetched_at, source, stale=True)
            raise

        fresh = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fetched_at": now,
            "data": data,
        }
        with self._lock:
            self._memory[cache_id] = fresh
            if cache_id.startswith("values:"):
                self._trim_memory_values()
        if self._config.jql_disk_cache_enabled:
            self._write_json(path, fresh)
            if cache_id.startswith("values:"):
                self._prune_value_files(path.parent, keep=path)
        return CacheResult(data, now, "jira")

    def _trim_memory_values(self) -> None:
        """Drop the oldest value snapshots. Callers must hold ``self._lock``."""

        value_keys = [key for key in self._memory if key.startswith("values:")]
        overflow = len(value_keys) - self._config.jql_value_cache_max_entries
        for key in value_keys[: max(0, overflow)]:
            self._memory.pop(key, None)
            # Keep the lock table bounded alongside the snapshots it guards. A
            # concurrent holder keeps its own reference, so dropping the entry
            # only means the next caller allocates a fresh lock.
            self._entry_locks.pop(key, None)

    def _prune_value_files(self, directory: Path, *, keep: Path) -> None:
        """Keep the newest value snapshots, always retaining the one just written.

        ``keep`` is pinned explicitly because filesystems with coarse mtime
        granularity can report the fresh file as no newer than its neighbours,
        which would otherwise let the pass delete what it just cached.
        """

        try:
            candidates = []
            for path in directory.glob("*.json"):
                if path == keep:
                    continue
                try:
                    candidates.append((path.stat().st_mtime_ns, path))
                except OSError:
                    # A concurrent process may prune the same directory.
                    continue
            candidates.sort(reverse=True)
            # `keep` occupies one of the retained slots.
            for _, path in candidates[max(self._config.jql_value_cache_max_entries - 1, 0) :]:
                path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not prune Jira JQL value cache %s: %s", directory, exc)

    def _invalidated_at(self) -> float:
        with self._lock:
            marker = self._memory.get("__invalidated__")
        if self._config.jql_disk_cache_enabled:
            disk_marker = self._read_invalidation_marker()
            try:
                disk_time = float((disk_marker or {}).get("invalidated_at", 0))
                memory_time = float((marker or {}).get("invalidated_at", 0))
            except (TypeError, ValueError):
                disk_time = memory_time = 0.0
            if disk_time > memory_time:
                marker = disk_marker
                with self._lock:
                    self._memory["__invalidated__"] = disk_marker
        try:
            return float((marker or {}).get("invalidated_at", 0))
        except (TypeError, ValueError):
            return 0.0

    def _read_invalidation_marker(self) -> dict | None:
        """Read the cross-process marker, re-parsing only when the file changed.

        This runs on every cache hit, so it stats the marker and reuses the
        previous parse while the file's identity is unchanged. Another process
        writes the marker via ``os.replace``, which always yields a new
        (inode, mtime, size) triple, so a real invalidation is never missed.
        """

        path = self._invalidation_path()
        try:
            status = path.stat()
            fingerprint = (status.st_ino, status.st_mtime_ns, status.st_size)
        except OSError:
            fingerprint = None
        with self._lock:
            if fingerprint is not None and fingerprint == self._marker_fingerprint:
                return self._marker_value
        value = self._read_json(path) if fingerprint is not None else None
        with self._lock:
            self._marker_fingerprint = fingerprint
            self._marker_value = value
        return value

    def _read_snapshot(self, path: Path) -> dict | None:
        snapshot = self._read_json(path)
        if snapshot is None:
            return None
        if (
            snapshot.get("schema_version") != CACHE_SCHEMA_VERSION
            or not isinstance(snapshot.get("data"), dict)
        ):
            LOGGER.warning("Ignoring incompatible Jira JQL cache snapshot: %s", path)
            return None
        try:
            float(snapshot["fetched_at"])
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("Ignoring Jira JQL cache snapshot without a valid timestamp: %s", path)
            return None
        return snapshot

    def _read_json(self, path: Path) -> dict | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise TypeError("cache root must be an object")
            return value
        except FileNotFoundError:
            return None
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("Ignoring unreadable Jira JQL cache file %s: %s", path, exc)
            return None

    def _write_json(self, path: Path, value: dict) -> None:
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{path.name}-",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temporary_path.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary_path, path)
            temporary_path = None
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("Could not persist Jira JQL cache file %s: %s", path, exc)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fields_path(self) -> Path:
        return self._config.jql_cache_dir / f"{self._instance_key}-fields.json"

    def _values_path(self, query_key: str) -> Path:
        return self._config.jql_cache_dir / f"{self._instance_key}-values" / f"{query_key}.json"

    def _invalidation_path(self) -> Path:
        return self._config.jql_cache_dir / f"{self._instance_key}-invalidated.json"
