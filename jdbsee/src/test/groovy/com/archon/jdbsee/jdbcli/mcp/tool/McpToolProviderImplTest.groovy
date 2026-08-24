package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import org.junit.Test

import static org.junit.Assert.*

class McpToolProviderImplTest {

  @Test
  void configuredLimitsReachTheCacheAndQueryTool() {
    def cache = new QueryResultCache()
    def provider = new McpToolProviderImpl()
    provider.cache = cache

    provider.configureLimits(45, 123)
    provider.init()

    assertEquals(45, cache.timeoutSeconds)
    def queryToolField = McpToolProviderImpl.getDeclaredField('queryTool')
    queryToolField.accessible = true
    assertEquals(123, (queryToolField.get(provider) as QueryTool).maxResults)
  }

  @Test
  void configuredLimitsMustBePositive() {
    def provider = new McpToolProviderImpl()
    provider.cache = new QueryResultCache()

    assertInvalidLimit { provider.configureLimits(0, 100) }
    assertInvalidLimit { provider.configureLimits(30, 0) }
  }

  private static void assertInvalidLimit(Closure operation) {
    try {
      operation.call()
      fail('Expected IllegalArgumentException')
    } catch (IllegalArgumentException expected) {
      assertNotNull(expected.message)
    }
  }
}
