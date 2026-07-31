"""Dependency-free memory and JSON snapshot cache for Jira JQL metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
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

    def get_fields(self, loader: Callable[[], dict], *, refresh: bool = False) -> CacheResult:
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
        return self._get(
            cache_id,
            self._values_path(cache_id.removeprefix("values:")),
            self._config.jql_value_refresh_interval,
            loader,
            refresh=refresh,
        )

    def invalidate(self) -> None:
        """Mark snapshots stale across MCP processes after a JQL validation error."""

        invalidated_at = self._clock()
        self._memory["__invalidated__"] = {"invalidated_at": invalidated_at}
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
        snapshot = self._memory.get(cache_id)
        source = "memory"
        if snapshot is None and self._config.jql_disk_cache_enabled:
            snapshot = self._read_snapshot(path)
            source = "disk"
            if snapshot is not None:
                self._memory[cache_id] = snapshot

        invalidated_at = self._invalidated_at()
        if snapshot is not None:
            fetched_at = float(snapshot["fetched_at"])
            age = max(0.0, now - fetched_at)
            is_invalidated = fetched_at < invalidated_at
            if not refresh and not is_invalidated and age < refresh_interval:
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
        self._memory[cache_id] = fresh
        if cache_id.startswith("values:"):
            self._trim_memory_values()
        if self._config.jql_disk_cache_enabled:
            self._write_json(path, fresh)
            if cache_id.startswith("values:"):
                self._prune_value_files(path.parent)
        return CacheResult(data, now, "jira")

    def _trim_memory_values(self) -> None:
        value_keys = [key for key in self._memory if key.startswith("values:")]
        overflow = len(value_keys) - self._config.jql_value_cache_max_entries
        for key in value_keys[: max(0, overflow)]:
            self._memory.pop(key, None)

    def _prune_value_files(self, directory: Path) -> None:
        try:
            paths = sorted(
                directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for path in paths[self._config.jql_value_cache_max_entries :]:
                path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not prune Jira JQL value cache %s: %s", directory, exc)

    def _invalidated_at(self) -> float:
        marker = self._memory.get("__invalidated__")
        if self._config.jql_disk_cache_enabled:
            disk_marker = self._read_json(self._invalidation_path())
            try:
                disk_time = float((disk_marker or {}).get("invalidated_at", 0))
                memory_time = float((marker or {}).get("invalidated_at", 0))
            except (TypeError, ValueError):
                disk_time = memory_time = 0.0
            if disk_time > memory_time:
                marker = disk_marker
                self._memory["__invalidated__"] = disk_marker
        try:
            return float((marker or {}).get("invalidated_at", 0))
        except (TypeError, ValueError):
            return 0.0

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
