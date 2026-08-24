package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.model.QueryResult
import com.archon.jdbsee.jdbcli.security.ValidationResult
import groovy.json.JsonOutput
import groovy.util.logging.Slf4j

import javax.inject.Singleton

/**
 * Formats query results as JSON, primarily for AI toolchain consumption.
 */
@Singleton
@Slf4j
class JsonAiResponseFormatter implements AiResponseFormatter {

  @Override
  String formatSuccess(QueryResult result, OutputFormat format, MetadataLevel level) {
    def response = [
      success : true,
      data    : buildData(result, level),
      metadata: buildMetadata(result)
    ]
    return JsonOutput.toJson(response)
  }

  @Override
  String formatError(ValidationResult error, String query) {
    def response = [
      success: false,
      error  : [
        code   : error.errorCode,
        message: error.message,
        details: error.details
      ],
      metadata: [
        query    : query,
        timestamp: System.currentTimeMillis()
      ]
    ]
    return JsonOutput.toJson(response)
  }

  @Override
  String formatException(Exception e, String context) {
    def response = [
      success: false,
      error  : [
        code   : 'SYS_500',
        message: 'Internal error occurred',
        details: e.message
      ],
      metadata: [
        context  : context,
        timestamp: System.currentTimeMillis()
      ]
    ]
    return JsonOutput.toJson(response)
  }

  private Map buildData(QueryResult result, MetadataLevel level) {
    def safeRows = result.rows.collect { row ->
      row.collect { it -> sanitizeValue(it) }
    }
    def objects = safeRows.collect { row ->
      def obj = [:]
      result.columns.eachWithIndex { col, idx -> obj[col] = row[idx] }
      obj
    }
    def data = [
      columns : result.columns,
      types   : result.columnTypes,
      rows    : safeRows,
      objects : objects,
      rowCount: result.rowCount
    ]
    if (level >= MetadataLevel.ENHANCED) {
      def metadata = [:]
      if (result.tableName) {
        metadata.tableName = result.tableName
      }
      if (result.schema) {
        metadata.schema = result.schema
      }
      if (result.database) {
        metadata.database = result.database
      }
      if (metadata) {
        data.metadata = metadata
      }
    }
    if (level == MetadataLevel.FULL) {
      if (!data.metadata) {
        data.metadata = [:]
      }
      if (result.statistics) {
        data.metadata.statistics = result.statistics
      }
      if (result.indexes) {
        data.metadata.indexes = result.indexes
      }
    }
    return data
  }

  private Map buildMetadata(QueryResult result) {
    return [
      executionTime: -1,
      rowCount     : result.rowCount,
      columnCount  : result.columnCount
    ]
  }

  private Object sanitizeValue(Object value) {
    if (value == null) {
      return null
    }
    if (value instanceof Number) {
      return value
    }
    if (value instanceof Boolean) {
      return value
    }
    if (value instanceof String) {
      return value
    }
    if (value instanceof Character) {
      return value.toString()
    }
    if (value instanceof Map) {
      return value.collectEntries { k, v -> [(k): sanitizeValue(v)] }
    }
    if (value instanceof Collection) {
      return value.collect { it -> sanitizeValue(it) }
    }
    if (value.getClass().isArray()) {
      return value.collect { it -> sanitizeValue(it) }
    }
    if (value instanceof Date) {
      return value.toString()
    }
    return value.toString()
  }
}
