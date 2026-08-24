package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject

/**
 * The "get_result" tool: returns a paged slice of a previously cached
 * query result (identified by {@code result_id}).
 */
class ResultTool {
  private static final Logger LOG = LoggerFactory.getLogger(ResultTool)

  @Inject
  QueryResultCache cache

  private String defaultAlias

  @Inject
  ResultTool() {}

  void setCache(QueryResultCache cache) { this.cache = cache }
  void setDefaultAlias(String defaultAlias) { this.defaultAlias = defaultAlias }

  Map<String, Object> execute(Map<String, Object> arguments) {
    String resultId = arguments.result_id as String
    Integer offsetArg = arguments.offset as Integer
    int offset = Math.max(0, offsetArg != null ? offsetArg : 0)
    Integer limitArg = arguments.limit as Integer
    int limit = Math.min(1000, Math.max(1, limitArg != null ? limitArg : 100))

    if (!resultId) {
      return errorResult("INVALID_ARGUMENT", "result_id is required")
    }

    def cachedResultOpt = cache.get(resultId)
    if (!cachedResultOpt.present) {
      return errorResult("RESULT_NOT_FOUND", "Result not found or expired: ${resultId}")
    }

    def cachedResult = cachedResultOpt.get()
    def allData = cachedResult.data ?: []
    int end = Math.min(offset + limit, allData.size())
    def pageData = offset < allData.size() ? allData.subList(offset, end) : []

    LOG.debug("返回结果 {}: 行 {}-{}/{}", resultId, offset + 1, end, allData.size())
    return [
        success   : true,
        result_id : resultId,
        alias     : cachedResult.alias,
        data      : pageData,
        offset    : offset,
        limit     : limit,
        total_rows: allData.size(),
        has_more  : end < allData.size()
    ]
  }

  private Map<String, Object> errorResult(String code, String message) {
    return [success: false, error: [code: code, message: message]]
  }
}
