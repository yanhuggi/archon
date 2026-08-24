package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.exception.CommandFailedException
import com.archon.jdbsee.jdbcli.format.AiResponseFormatter
import com.archon.jdbsee.jdbcli.format.CsvAiResponseFormatter
import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.format.JsonAiResponseFormatter
import com.archon.jdbsee.jdbcli.format.MarkdownAiResponseFormatter
import com.archon.jdbsee.jdbcli.security.SqlSecurityValidator
import com.archon.jdbsee.jdbcli.security.ValidationResult
import org.jdbi.v3.core.Jdbi
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton
import javax.sql.DataSource

@Singleton
class QueryService {

  private static final Logger LOG = LoggerFactory.getLogger(QueryService)

  private final SqlSecurityValidator securityValidator
  private final FormatterFactory formatterFactory

  @Inject
  QueryService(SqlSecurityValidator securityValidator, FormatterFactory formatterFactory) {
    this.securityValidator = securityValidator
    this.formatterFactory = formatterFactory
  }

  ValidationResult validate(String sql) {
    securityValidator.validate(sql)
  }

  void execute(DataSource dataSource, QueryCallback<Void, Exception> callback, String... sqlArray) throws Exception {
    def jdbi = dataSource != null ? Jdbi.create(dataSource) : null
    if (jdbi == null) {
      throw new IllegalArgumentException("DataSource cannot be null")
    }
    execute(jdbi, callback, sqlArray)
  }

  /**
   * Runs each statement in turn. A rejected statement is reported and skipped
   * while the remaining ones still run — the legacy execution strategy is kept
   * deliberately, so an existing script does not silently execute fewer
   * queries than before.
   *
   * The command still fails: after every statement has been processed, the
   * first rejection is raised so the process exits non-zero.
   */
  void execute(Jdbi jdbi, QueryCallback<Void, Exception> callback, String... sqlArray) throws Exception {
    ValidationResult firstRejection = null
    for (String sql : sqlArray) {
      ValidationResult rejection = executeQuery(jdbi, callback, sql)
      if (rejection != null && firstRejection == null) {
        firstRejection = rejection
      }
    }
    if (firstRejection != null) {
      // Raised here rather than inside executeQuery so it cannot be caught and
      // re-reported by that method's generic handler.
      throw new CommandFailedException(new IllegalStateException(
          "${firstRejection.errorCode}: ${firstRejection.message}"))
    }
  }

  /**
   * Reports and skips a rejected statement, returning the rejection; returns
   * null when the statement ran. Execution errors still propagate immediately.
   */
  private ValidationResult executeQuery(Jdbi jdbi, QueryCallback<Void, Exception> callback, String sql) throws Exception {
    ValidationResult validationResult
    try {
      validationResult = securityValidator.validate(sql)
    } catch (Exception e) {
      outputException(e, "query execution")
      throw e
    }
    if (!validationResult.valid) {
      outputError(validationResult, sql)
      return validationResult
    }
    jdbi.useHandle { handle ->
      def query = handle.createQuery(sql)
      callback.doWithQuery(query)
    }
    return null
  }

  AiResponseFormatter getFormatter(String formatCode) {
    if (formatterFactory == null) {
      LOG.warn("FormatterFactory not injected, using default formatter")
      def code = formatCode?.trim()?.toLowerCase()
      if (code == 'markdown') {
        return new MarkdownAiResponseFormatter()
      }
      if (code == 'csv') {
        return new CsvAiResponseFormatter()
      }
      return new JsonAiResponseFormatter()
    }
    return formatterFactory.getFormatter(formatCode)
  }

  private void outputError(ValidationResult error, String query) {
    def formatter = getFormatter('json')
    def output = formatter.formatError(error, query)
    System.out.println(output)
  }

  private void outputException(Exception e, String context) {
    def formatter = getFormatter('json')
    def output = formatter.formatException(e, context)
    System.out.println(output)
  }
}
