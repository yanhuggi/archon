package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.model.QueryResult
import com.archon.jdbsee.jdbcli.security.ValidationResult
import groovy.util.logging.Slf4j

import javax.inject.Singleton

/**
 * Formats query results as a Markdown document, suitable for AI toolchain consumption.
 */
@Singleton
@Slf4j
class MarkdownAiResponseFormatter implements AiResponseFormatter {

  @Override
  String formatSuccess(QueryResult result, OutputFormat format, MetadataLevel level) {
    def sb = new StringBuilder()
    sb << '# Query Results\n\n'
    sb << "**Row Count:** ${result.rowCount}\n"
    if (result.rowCount == 0) {
      sb << '**No rows returned**\n'
    }
    sb << '\n'

    if ((level == MetadataLevel.ENHANCED || level == MetadataLevel.FULL) && result.tableName) {
      sb << '## Metadata\n\n'
      sb << "**Table:** ${result.tableName}\n"
      if (result.schema) {
        sb << "**Schema:** ${result.schema}\n"
      }
      if (result.database) {
        sb << "**Database:** ${result.database}\n"
      }
      sb << '\n'
    }

    if (level == MetadataLevel.FULL) {
      sb << '## Column Types\n\n'
      result.columns.eachWithIndex { col, idx ->
        def type = idx < (result.columnTypes?.size() ?: 0) ? result.columnTypes[idx] : 'unknown'
        sb << "- ${col}: ${type}\n"
      }
      sb << '\n'
    }

    sb << '| '
    // A column label can contain a pipe (SELECT x AS "a|b"), which would add a
    // phantom column unless escaped like the values.
    sb << result.columns.collect { it -> formatValue(it) }.join(' | ')
    sb << ' |\n'
    sb << '|'
    // One separator per column, not per known type: a short columnTypes list
    // would otherwise produce a separator row that does not match the header.
    def separators = []
    result.columns.eachWithIndex { col, idx ->
      def type = idx < (result.columnTypes?.size() ?: 0) ? result.columnTypes[idx] : null
      separators << (isNumericType(type) ? '---:' : '---')
    }
    sb << separators.join('|')
    sb << '|\n'
    result.rows.each { row ->
      sb << '| '
      sb << row.collect { it -> formatValue(it) }.join(' | ')
      sb << ' |\n'
    }
    return sb.toString()
  }

  @Override
  String formatError(ValidationResult error, String query) {
    return "# ❌ Query Error\n\n**Error Code:** ${error.errorCode}\n**Message:** ${error.message}\n**Details:** ${error.details}\n\n**SQL:** ${query}\n"
  }

  @Override
  String formatException(Exception e, String context) {
    return "# ❌ System Error\n\n**Error Code:** SYS_500\n**Context:** ${context}\n**Message:** Internal error occurred\n**Details:** ${e.message}\n"
  }

  private boolean isNumericType(String type) {
    def numericTypes = ['INTEGER', 'DECIMAL', 'NUMERIC', 'BIGINT', 'SMALLINT', 'FLOAT', 'DOUBLE', 'REAL', 'INT']
    return type != null && numericTypes.any { it -> type.toUpperCase().contains(it) }
  }

  private String formatValue(Object value) {
    if (value == null) {
      return 'NULL'
    }
    String str = value.toString()
    str = str.replace('|', '\\|')
    return str
  }
}
