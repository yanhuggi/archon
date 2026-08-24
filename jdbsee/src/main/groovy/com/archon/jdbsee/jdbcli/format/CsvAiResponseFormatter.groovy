package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.model.QueryResult
import com.archon.jdbsee.jdbcli.security.ValidationResult
import groovy.util.logging.Slf4j

import javax.inject.Singleton

/**
 * Formats query results as comma-separated values, suitable for AI toolchain consumption.
 */
@Singleton
@Slf4j
class CsvAiResponseFormatter implements AiResponseFormatter {

  @Override
  String formatSuccess(QueryResult result, OutputFormat format, MetadataLevel level) {
    def sb = new StringBuilder()
    if (level == MetadataLevel.ENHANCED || level == MetadataLevel.FULL) {
      sb << '# Query Metadata\n'
      if (result.tableName != null) {
        sb << "# table:${result.tableName}\n"
      }
      if (result.schema != null) {
        sb << "# schema:${result.schema}\n"
      }
      if (result.database != null) {
        sb << "# database:${result.database}\n"
      }
      sb << "# rowCount:${result.rowCount}\n"
      sb << "# columnCount:${result.columnCount}\n"
    }

    if (level == MetadataLevel.FULL) {
      sb << '# columnTypes:'
      def typeInfo = []
      result.columns.eachWithIndex { col, idx ->
        // Only emit a type when the driver reported one for this column.
        def type = idx < (result.columnTypes?.size() ?: 0) ? result.columnTypes[idx] : null
        typeInfo << (type != null ? "${col}:${type}" : "${col}")
      }
      sb << typeInfo.join(',')
      sb << '\n'
    }

    // Column labels come from the query (SELECT x AS "a,b"), so they need the
    // same quoting as the values; otherwise the header row gains extra fields.
    sb << result.columns.collect { it -> formatCsvValue(it) }.join(',')
    sb << '\n'
    result.rows.each { row ->
      sb << row.collect { it -> formatCsvValue(it) }.join(',')
      sb << '\n'
    }
    return sb.toString()
  }

  @Override
  String formatError(ValidationResult error, String query) {
    return "# ERROR\ncode:${error.errorCode}\nmessage:${error.message}\ndetails:${error.details}\n"
  }

  @Override
  String formatException(Exception e, String context) {
    return "# SYSTEM_ERROR\ncode:SYS_500\nmessage:Internal error occurred\ncontext:${context}\ndetails:${e.message}\n"
  }

  private String formatCsvValue(Object value) {
    if (value == null) {
      return ''
    }
    String strValue = value.toString()
    if (strValue.contains(',') || strValue.contains('"') || strValue.contains('\n')) {
      return "\"${strValue.replace('"', '""')}\""
    }
    return strValue
  }
}
