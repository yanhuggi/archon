package com.archon.jdbsee.jdbcli.model

import groovy.transform.EqualsAndHashCode
import groovy.transform.ToString

/**
 * Cached query result used by the MCP result retrieval flow.
 */
@ToString
@EqualsAndHashCode
class CachedResult {
    String resultId
    String alias
    List<Map<String, Object>> data
    Map<String, Object> metadata
    long createdAt

    CachedResult(String resultId, String alias, List<Map<String, Object>> data, Map<String, Object> metadata, long createdAt) {
        this.resultId = resultId
        this.alias = alias
        this.data = data
        this.metadata = metadata
        this.createdAt = createdAt
    }

    CachedResult(String resultId, String alias, List<Map<String, Object>> data, Map<String, Object> metadata) {
        this(resultId, alias, data, metadata, 0L)
    }

    CachedResult(String resultId, String alias, List<Map<String, Object>> data) {
        this(resultId, alias, data, null, 0L)
    }

    CachedResult(String resultId, String alias) {
        this(resultId, alias, null, null, 0L)
    }

    CachedResult(String resultId) {
        this(resultId, null, null, null, 0L)
    }

    CachedResult() {
        this(null, null, null, null, 0L)
    }
}
