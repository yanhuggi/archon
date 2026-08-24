package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.QueryService
import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import com.archon.jdbsee.jdbcli.model.CachedResult
import org.jdbi.v3.core.Jdbi
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import java.util.UUID

/**
 * The "execute_query" tool: validates the SQL, runs it against the
 * (cached) {@link Jdbi} handle for the effective alias and stores the
 * rows in the {@link QueryResultCache}, returning the result id plus
 * metadata instead of the full dataset.
 */
class QueryTool {
  private static final Logger LOG = LoggerFactory.getLogger(QueryTool)

  @Inject
  QueryService queryService

  @Inject
  QueryResultCache cache

  @Inject
  JdbiCache jdbiCache

  private String defaultAlias

  int maxResults = 10000

  @Inject
  QueryTool() {}

  void setQueryService(QueryService queryService) { this.queryService = queryService }
  void setCache(QueryResultCache cache) { this.cache = cache }
  void setJdbiCache(JdbiCache jdbiCache) { this.jdbiCache = jdbiCache }
  void setDefaultAlias(String defaultAlias) { this.defaultAlias = defaultAlias }
  void setMaxResults(int maxResults) { this.maxResults = maxResults }

  Map<String, Object> execute(Map<String, Object> arguments) {
    String sql = arguments.sql as String
    String formatStr = arguments.format ?: "json"
    String metadataLevelStr = arguments.metadata_level ?: "basic"
    String alias = arguments.alias as String

    if (!sql) {
      return errorResult("INVALID_ARGUMENT", "SQL query is required")
    }

    def validationResult = queryService.validate(sql)
    if (!validationResult.valid) {
      return errorResult("SECURITY_VALIDATION_FAILED", validationResult.message)
    }

    try {
      String effectiveAlias = getEffectiveAlias(alias)
      Jdbi jdbi = getJdbiForAlias(effectiveAlias)
      def queryResult = executeQuery(jdbi, sql, formatStr, metadataLevelStr)

      String resultId = UUID.randomUUID().toString()
      def cachedResult = new CachedResult(
          resultId: resultId,
          alias: effectiveAlias,
          data: queryResult.data,
          metadata: queryResult.metadata,
          createdAt: System.currentTimeMillis()
      )
      cache.put(cachedResult)

      return [
          success  : true,
          result_id: resultId,
          metadata : [
              row_count   : queryResult.data.size(),
              column_count: queryResult.metadata.columnCount,
              columns     : queryResult.metadata.columns,
              truncated   : queryResult.metadata.truncated,
              max_results : queryResult.metadata.maxResults,
              query       : sql
          ]
      ]
    } catch (IllegalArgumentException e) {
      // Unknown alias or no alias configured: a caller-side problem.
      LOG.warn("Query rejected: {}", e.message)
      return errorResult("INVALID_ARGUMENT", e.message)
    } catch (Exception e) {
      LOG.error("Query execution error", e)
      return errorResult("QUERY_EXECUTION_ERROR", e.message)
    }
  }

  private String getEffectiveAlias(String alias) {
    String effectiveAlias = alias ?: defaultAlias
    if (!effectiveAlias) {
      throw new IllegalArgumentException("No database alias specified. Please provide an 'alias' parameter or set a default alias.")
    }
    return effectiveAlias
  }

  private Jdbi getJdbiForAlias(String alias) {
    try {
      return jdbiCache.getJdbi(alias)
    } catch (IllegalArgumentException e) {
      throw new IllegalArgumentException("Database alias '${alias}' not found. Use the 'list_aliases' tool to view available aliases, or create a new alias first.")
    }
  }

  /**
   * Runs the SQL once and streams the ResultSet, stopping as soon as
   * maxResults rows have been collected so that an oversized result cannot
   * exhaust the heap. Column labels and types come from the same ResultSet,
   * so the statement is never executed twice.
   */
  private InternalQueryResult executeQuery(Jdbi jdbi, String sql, String formatStr, String metadataLevelStr) {
    // Validate / normalise the requested format and metadata level.
    OutputFormat format = OutputFormat.fromCode(formatStr)
    MetadataLevel metadataLevel = MetadataLevel.fromString(metadataLevelStr)

    return jdbi.withHandle { handle ->
      def stmt = handle.connection.prepareStatement(sql)
      try {
        // Hint the driver to fetch in pages rather than the whole result at once.
        try {
          stmt.fetchSize = Math.min(maxResults, 1000)
        } catch (Exception ignored) {
          // Not all drivers honour fetchSize; carry on regardless.
        }
        if (maxResults > 0 && maxResults < Integer.MAX_VALUE) {
          try {
            // Cap at one row beyond the limit: the extra row is never returned to
            // the caller, it only proves the result set was larger than the cap.
            stmt.maxRows = maxResults + 1
          } catch (Exception ignored) {
          }
        }
        def rs = stmt.executeQuery()
        try {
          def metaData = rs.metaData
          int columnCount = metaData.columnCount
          List columnNames = []
          List columnTypes = []
          for (int i = 1; i <= columnCount; i++) {
            columnNames << metaData.getColumnLabel(i)
            columnTypes << metaData.getColumnClassName(i)
          }

          List<Map<String, Object>> data = []
          boolean truncated = false
          while (rs.next()) {
            if (data.size() >= maxResults) {
              // There is at least one more row than the cap allows.
              truncated = true
              break
            }
            def row = [:]
            for (int i = 1; i <= columnCount; i++) {
              row[columnNames[i - 1]] = rs.getObject(i)
            }
            data << row
          }

          def metadata = [
              rowCount    : data.size(),
              columnCount : columnCount,
              columns     : columnNames,
              columnTypes : columnTypes,
              query       : sql,
              truncated   : truncated,
              maxResults  : maxResults
          ]
          return new InternalQueryResult(data: data, metadata: metadata)
        } finally {
          rs.close()
        }
      } finally {
        stmt.close()
      }
    }
  }

  private Map<String, Object> errorResult(String code, String message) {
    return [success: false, error: [code: code, message: message]]
  }

  private static class InternalQueryResult {
    List<Map<String, Object>> data
    Map<String, Object> metadata
  }
}
