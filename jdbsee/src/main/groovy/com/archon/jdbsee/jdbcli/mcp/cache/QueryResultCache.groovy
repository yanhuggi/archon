package com.archon.jdbsee.jdbcli.mcp.cache

import com.archon.jdbsee.jdbcli.model.CachedResult
import com.github.benmanes.caffeine.cache.Cache
import com.github.benmanes.caffeine.cache.Caffeine

import java.util.concurrent.TimeUnit

/**
 * Caffeine-backed cache for query results, keyed by result id.
 *
 * Entries expire after {@link #timeoutSeconds} and the cache is bounded
 * by {@link #maxSize} (LRU eviction).
 */
class QueryResultCache {

  int timeoutSeconds = 300
  int maxSize = 1000

  private Cache<String, CachedResult> cache

  Cache<String, CachedResult> getCache() {
    if (cache == null) {
      cache = Caffeine.newBuilder()
          .maximumSize(maxSize)
          .expireAfterWrite(timeoutSeconds, TimeUnit.SECONDS)
          .build()
    }
    return cache
  }

  void put(CachedResult result) {
    if (result?.resultId) {
      getCache().put(result.resultId, result)
    }
  }

  Optional<CachedResult> get(String resultId) {
    Optional.ofNullable(getCache().getIfPresent(resultId))
  }

  void remove(String resultId) {
    getCache().invalidate(resultId)
  }

  void clear() {
    getCache().invalidateAll()
  }

  long size() {
    getCache().estimatedSize()
  }
}
