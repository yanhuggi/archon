package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.security.DmlSecurityValidator
import com.archon.jdbsee.jdbcli.security.ValidationResult
import groovy.json.JsonOutput
import net.sf.jsqlparser.parser.CCJSqlParserUtil
import net.sf.jsqlparser.statement.Statements
import net.sf.jsqlparser.statement.delete.Delete
import net.sf.jsqlparser.statement.insert.Insert
import net.sf.jsqlparser.statement.select.Select
import net.sf.jsqlparser.statement.update.Update
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton
import javax.sql.DataSource
import java.sql.Connection
import java.sql.Statement

@Singleton
class ExecuteService {

  private static final Logger LOG = LoggerFactory.getLogger(ExecuteService)
  private static final Logger log = LoggerFactory.getLogger('com.archon.jdbsee.jdbcli.ExecuteService')

  private final DmlSecurityValidator securityValidator
  private final FormatterFactory formatterFactory

  @Inject
  ExecuteService(DmlSecurityValidator securityValidator, FormatterFactory formatterFactory) {
    this.securityValidator = securityValidator
    this.formatterFactory = formatterFactory
  }

  Map<String, Object> execute(DataSource dataSource, String sql, String formatCode) {
    if (dataSource == null) {
      throw new IllegalArgumentException('DataSource cannot be null')
    }
    def validationResult = securityValidator.validate(sql)
    if (!validationResult.isValid()) {
      return buildErrorResult(validationResult, sql)
    }
    Connection connection = null
    try {
      connection = dataSource.getConnection()
      if (connection.isReadOnly()) {
        connection.setReadOnly(false)
      }
      return executeWithConnection(connection, sql)
    } catch (Exception e) {
      LOG.error('Execute failed: {}', e.message, e)
      return buildExceptionResult(e, sql)
    } finally {
      if (connection != null) {
        try {
          connection.close()
        } catch (Exception ignored) {
        }
      }
    }
  }

  String executeAndFormat(DataSource dataSource, String sql, String formatCode) {
    def result = execute(dataSource, sql, formatCode)
    return formatResult(result, formatCode)
  }

  private Map<String, Object> executeWithConnection(Connection connection, String sql) {
    def validationResult = securityValidator.validate(sql)
    if (!validationResult.isValid()) {
      return buildErrorResult(validationResult, sql)
    }
    def statementType = detectStatementType(sql)
    Statement stmt = null
    try {
      stmt = connection.createStatement()
      int rowsAffected = stmt.executeUpdate(sql)
      return buildSuccessResult(statementType, rowsAffected, sql)
    } catch (Exception e) {
      LOG.error('Execute failed: {}', e.message, e)
      return buildExceptionResult(e, sql)
    } finally {
      if (stmt != null) {
        try {
          stmt.close()
        } catch (Exception ignored) {
        }
      }
    }
  }

  private String detectStatementType(String sql) {
    try {
      def stmt = CCJSqlParserUtil.parse(sql)
      if (stmt instanceof Insert || stmt instanceof Update || stmt instanceof Delete) {
        return 'DML'
      }
      if (stmt instanceof Select) {
        return 'SELECT'
      }
      return 'DDL'
    } catch (Exception e) {
      LOG.debug('Cannot detect statement type, defaulting to DDL: {}', e.message)
      return 'DDL'
    }
  }

  List<String> splitSqlStatements(String sqlText) {
    def trimmed = sqlText?.trim()
    if (!trimmed) {
      return Collections.emptyList()
    }
    try {
      Statements statements = CCJSqlParserUtil.parseStatements(sqlText)
      def result = []
      for (stmt in statements.statements) {
        if (stmt != null) {
          def sql = stmt.toString().trim()
          if (sql) {
            result.add(sql)
          }
        }
      }
      return result
    } catch (Exception e) {
      LOG.debug('JSqlParser failed to split, falling back to semicolon split: {}', e.message)
      return splitBySemicolon(sqlText)
    }
  }

  private List<String> splitBySemicolon(String sqlText) {
    def result = []
    for (part in sqlText.split(';')) {
      def trimmed = part.trim()
      def withoutComments = trimmed.replaceAll('(?m)^\\s*--.*$', '').trim()
      if (withoutComments) {
        result.add(withoutComments)
      }
    }
    return result
  }

  /** Formatted batch output plus whether anything in it failed. */
  static class BatchOutcome {
    String output
    boolean failed
  }

  /**
   * Detects a block body that statement splitting has torn apart.
   *
   * jsqlparser keeps CREATE PROCEDURE/FUNCTION bodies intact, but a
   * CREATE TRIGGER ... BEGIN a; b; END is split into fragments: the inner
   * statements would then run standalone against the tables while the opening
   * and closing fragments fail as syntax errors. Refusing is the only safe
   * option, since running part of a trigger body silently modifies data.
   */
  static boolean hasSplitBlockBody(List<String> statements) {
    if (statements.size() < 2) {
      return false
    }
    return statements.any { String sql ->
      String normalized = sql?.trim()?.toUpperCase()
      if (!normalized) {
        return false
      }
      // A lone END fragment, or a fragment that opens a block without closing
      // it — both mean the body was cut at a statement boundary inside it.
      if (normalized ==~ /(?s)END\s*;?/) {
        return true
      }
      int begins = countWord(normalized, 'BEGIN')
      int ends = countWord(normalized, 'END')
      return begins > ends
    }
  }

  private static int countWord(String text, String word) {
    int count = 0
    def matcher = (text =~ /\b${word}\b/)
    while (matcher.find()) {
      count++
    }
    return count
  }

  /**
   * Same as {@link #executeBatchAndFormat} but also reports whether the run
   * failed, so the caller can set a non-zero exit status.
   */
  BatchOutcome executeBatchWithOutcome(DataSource dataSource, String sqlText, String formatCode) {
    def statements = splitSqlStatements(sqlText)
    if (statements.isEmpty()) {
      return new BatchOutcome(output: formatResult(buildEmptyInputResult(), formatCode), failed: true)
    }
    if (hasSplitBlockBody(statements)) {
      return new BatchOutcome(output: formatResult(buildSplitBlockResult(sqlText), formatCode), failed: true)
    }
    if (statements.size() == 1) {
      def result = execute(dataSource, statements[0], formatCode)
      return new BatchOutcome(output: formatResult(result, formatCode), failed: !result.success)
    }
    def batch = runBatch(dataSource, statements)
    return new BatchOutcome(
        output: formatBatchResult(batch, formatCode),
        failed: (batch.summary.failure_count as int) > 0
    )
  }

  String executeBatchAndFormat(DataSource dataSource, String sqlText, String formatCode) {
    def statements = splitSqlStatements(sqlText)
    if (statements.isEmpty()) {
      return formatResult(buildEmptyInputResult(), formatCode)
    }
    if (hasSplitBlockBody(statements)) {
      return formatResult(buildSplitBlockResult(sqlText), formatCode)
    }
    if (statements.size() == 1) {
      return executeAndFormat(dataSource, statements[0], formatCode)
    }
    return formatBatchResult(runBatch(dataSource, statements), formatCode)
  }

  /**
   * Runs every statement on one shared connection. Statements are independent:
   * a failure is recorded and the run continues, and there is no transaction
   * around the batch.
   */
  private Map<String, Object> runBatch(DataSource dataSource, List<String> statements) {
    def results = []
    int successCount = 0
    int failureCount = 0
    Connection batchConnection = null
    try {
      batchConnection = dataSource.getConnection()
      if (batchConnection.isReadOnly()) {
        batchConnection.setReadOnly(false)
      }
      for (int i = 0; i < statements.size(); i++) {
        def sql = statements[i]
        def singleResult = executeWithConnection(batchConnection, sql)
        def indexedResult = new HashMap(singleResult)
        indexedResult.put('index', i)
        results.add(indexedResult)
        if (singleResult.success) {
          successCount++
        } else {
          failureCount++
        }
      }
    } catch (Exception e) {
      LOG.error('Batch connection failed: {}', e.message, e)
      def classified = ErrorMapper.toValidationResult(e)
      results.add([
          index : 0,
          success: false,
          error : [code: classified.errorCode, message: classified.message, details: classified.details]
      ])
      failureCount = statements.size()
    } finally {
      if (batchConnection != null) {
        try {
          batchConnection.close()
        } catch (Exception ignored) {
        }
      }
    }
    return [
        results: results,
        summary: [total: statements.size(), success_count: successCount, failure_count: failureCount]
    ]
  }

  private Map<String, Object> buildEmptyInputResult() {
    return [
        success: false,
        error : [code: 'SQL_100', message: 'No valid SQL statements found', details: 'The input contains no parseable SQL statements']
    ]
  }

  private Map<String, Object> buildSplitBlockResult(String sql) {
    return [
        success: false,
        error  : [
            code   : ErrorCode.DANGEROUS_DDL.getCode(),
            message: 'Block body cannot be executed safely',
            details: 'Statement splitting would tear this BEGIN ... END body apart and run ' +
                'its inner statements standalone. Apply triggers, procedures and function ' +
                'bodies with a database-native client instead.'
        ],
        sql    : sql
    ]
  }

  /**
   * Formats a failure that happened before any statement could run (typically a
   * connection or driver problem) using the requested output format.
   */
  String formatFailure(ValidationResult failure, String sql, String formatCode) {
    def result = [
        success: false,
        error  : [code: failure.errorCode, message: failure.message, details: failure.details],
        sql    : sql
    ]
    return formatResult(result, formatCode)
  }

  private String formatBatchResult(Map<String, Object> batchResult, String formatCode) {
    def code = formatCode?.trim()?.toLowerCase()
    if (code == 'markdown') {
      return formatBatchMarkdown(batchResult)
    }
    if (code == 'csv') {
      return formatBatchCsv(batchResult)
    }
    return JsonOutput.toJson(batchResult)
  }

  private String formatBatchMarkdown(Map<String, Object> batchResult) {
    def results = batchResult.results
    def summary = batchResult.summary
    StringBuilder sb = new StringBuilder()
    sb.append('# Execute Results\n\n')
    for (r in results) {
      int idx = r.index
      sb.append("## Statement ${idx + 1}\n")
      if (r.success) {
        sb.append("**Status:** Success | **Type:** ${r.statement_type} | **Rows Affected:** ${r.rows_affected}\n")
      } else {
        def error = r.error
        sb.append("**Status:** Failed | **Error:** ${error.code} - ${error.message}\n")
      }
      sb.append("**SQL:** ${r.sql}\n\n")
    }
    sb.append('---\n')
    sb.append("**Summary:** ${summary.total} total, ${summary.success_count} success, ${summary.failure_count} failure\n")
    return sb.toString()
  }

  private String formatBatchCsv(Map<String, Object> batchResult) {
    def results = batchResult.results
    StringBuilder sb = new StringBuilder()
    sb.append('index,success,statement_type,rows_affected,error_code,error_message,sql\n')
    for (r in results) {
      if (r.success) {
        sb.append("${r.index},true,\"${escapeCsv(r.statement_type as String)}\",${r.rows_affected},,,\"${escapeCsv(r.sql as String)}\"\n")
      } else {
        def error = r.error
        sb.append("${r.index},false,,,,\"${escapeCsv(error.code as String)} - ${escapeCsv(error.message as String)}\",\"${escapeCsv(r.sql as String)}\"\n")
      }
    }
    return sb.toString()
  }

  private String escapeCsv(String value) {
    if (value == null) {
      return ''
    }
    return value.replace('"', '""')
  }

  private Map<String, Object> buildSuccessResult(String statementType, int rowsAffected, String sql) {
    return [success: true, statement_type: statementType, rows_affected: rowsAffected, sql: sql]
  }

  private Map<String, Object> buildErrorResult(ValidationResult error, String sql) {
    return [
        success: false,
        error : [code: error.errorCode, message: error.message, details: error.details],
        sql   : sql
    ]
  }

  private Map<String, Object> buildExceptionResult(Exception e, String sql) {
    // Classify rather than labelling every failure DB_301: a connection problem
    // is DB_300 and a missing driver is CFG_200.
    def classified = ErrorMapper.toValidationResult(e)
    return [
        success: false,
        error : [code: classified.errorCode, message: classified.message, details: classified.details],
        sql   : sql
    ]
  }

  private String formatResult(Map<String, Object> result, String formatCode) {
    if (result.success) {
      return formatSuccessResult(result, formatCode)
    }
    return formatErrorResult(result, formatCode)
  }

  private String formatSuccessResult(Map<String, Object> result, String formatCode) {
    def code = formatCode?.trim()?.toLowerCase()
    if (code == 'markdown') {
      return "# Execute Result\n\n**Status:** Success\n**Statement Type:** ${result.statement_type}\n**Rows Affected:** ${result.rows_affected}\n\n**SQL:** ${result.sql}\n"
    }
    if (code == 'csv') {
      return "success,statement_type,rows_affected,sql\ntrue,${result.statement_type},${result.rows_affected},\"${result.sql}\""
    }
    return JsonOutput.toJson(result)
  }

  private String formatErrorResult(Map<String, Object> result, String formatCode) {
    def error = result.error as Map
    def code = formatCode?.trim()?.toLowerCase()
    if (code == 'markdown') {
      return "# Execute Error\n\n**Error Code:** ${error.code}\n**Message:** ${error.message}\n" +
          (error.details ? "**Details:** ${error.details}\n" : '') +
          "\n**SQL:** ${result.sql}\n"
    }
    if (code == 'csv') {
      return "success,error_code,message,sql\nfalse,${error.code},\"${error.message}\",\"${result.sql}\""
    }
    return JsonOutput.toJson(result)
  }
}
