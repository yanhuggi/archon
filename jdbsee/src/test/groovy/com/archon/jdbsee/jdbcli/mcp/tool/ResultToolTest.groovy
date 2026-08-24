package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import com.archon.jdbsee.jdbcli.model.CachedResult
import org.junit.Test

import static org.junit.Assert.*

class ResultToolTest {

  @Test
  void cachedResultsArePagedAndBounded() {
    def cache = new QueryResultCache()
    cache.put(new CachedResult(
        resultId: 'result-1',
        alias: 'local',
        data: [[id: 1], [id: 2], [id: 3]],
        createdAt: System.currentTimeMillis()
    ))
    def tool = new ResultTool(cache: cache)

    def page = tool.execute([result_id: 'result-1', offset: 1, limit: 1])

    assertTrue(page.success)
    assertEquals([[id: 2]], page.data)
    assertEquals(3, page.total_rows)
    assertTrue(page.has_more)
    assertFalse(tool.execute([result_id: 'missing']).success)
  }
}
