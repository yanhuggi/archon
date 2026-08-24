package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.SchemaService
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import org.jdbi.v3.core.Jdbi
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import java.sql.DatabaseMetaData

/**
 * Schema exploration tools: "list_tables" (with an optional name
 * pattern) and "describe_table" (columns, type, size, nullability).
 */
class SchemaTool {
  private static final Logger LOG = LoggerFactory.getLogger(SchemaTool)

  @Inject
  SchemaService schemaService

  @Inject
  JdbiCache jdbiCache

  private String defaultAlias

  @Inject
  SchemaTool() {}

  void setSchemaService(SchemaService schemaService) { this.schemaService = schemaService }
  void setJdbiCache(JdbiCache jdbiCache) { this.jdbiCache = jdbiCache }
  void setDefaultAlias(String defaultAlias) { this.defaultAlias = defaultAlias }

  Map<String, Object> execute(Map<String, Object> arguments) {
    String action = arguments.action as String
    String alias = arguments.alias as String
    switch (action) {
      case "list_tables":
        return listTables(alias, arguments)
      case "describe_table":
        return describeTable(alias, arguments)
      default:
        return errorResult("INVALID_ACTION", "Unknown action: ${action}")
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

  private Map<String, Object> listTables(String alias, Map<String, Object> arguments) {
    String patternStr = arguments.pattern as String
    def pattern = patternStr ?: "%"

    try {
      String effectiveAlias = getEffectiveAlias(alias)
      Jdbi jdbi = getJdbiForAlias(effectiveAlias)

      def tables = []
      jdbi.withHandle { handle ->
        def conn = handle.connection
        def dbMeta = conn.metaData
        def rs = dbMeta.getTables(null, null, pattern, ["TABLE"] as String[])
        try {
          while (rs.next()) {
            tables << [
                table_name   : rs.getString("TABLE_NAME"),
                table_schema : rs.getString("TABLE_SCHEM"),
                table_catalog: rs.getString("TABLE_CAT")
            ]
          }
        } finally {
          rs.close()
        }
      }
      return [success: true, tables: tables]
    } catch (IllegalArgumentException e) {
      LOG.warn("List tables rejected: {}", e.message)
      return errorResult("INVALID_ARGUMENT", e.message)
    } catch (Exception e) {
      LOG.error("Error listing tables", e)
      return errorResult("SCHEMA_ERROR", e.message)
    }
  }

  private Map<String, Object> describeTable(String alias, Map<String, Object> arguments) {
    String tableName = arguments.table_name as String
    if (!tableName) {
      return errorResult("INVALID_ARGUMENT", "table_name is required")
    }

    try {
      String effectiveAlias = getEffectiveAlias(alias)
      Jdbi jdbi = getJdbiForAlias(effectiveAlias)

      def columns = []
      jdbi.withHandle { handle ->
        def conn = handle.connection
        def dbMeta = conn.metaData
        def rs = dbMeta.getColumns(null, null, tableName, "%")
        try {
          while (rs.next()) {
            columns << [
                column_name : rs.getString("COLUMN_NAME"),
                data_type   : rs.getString("TYPE_NAME"),
                column_size : rs.getInt("COLUMN_SIZE"),
                nullable    : rs.getInt("NULLABLE") == DatabaseMetaData.columnNullable
            ]
          }
        } finally {
          rs.close()
        }
      }
      return [success: true, table_name: tableName, columns: columns]
    } catch (IllegalArgumentException e) {
      LOG.warn("Describe table rejected: {}", e.message)
      return errorResult("INVALID_ARGUMENT", e.message)
    } catch (Exception e) {
      LOG.error("Error describing table", e)
      return errorResult("SCHEMA_ERROR", e.message)
    }
  }

  private Map<String, Object> errorResult(String code, String message) {
    return [success: false, error: [code: code, message: message]]
  }
}
