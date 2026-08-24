package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.exception.CommandFailedException
import com.archon.jdbsee.jdbcli.model.QueryResult
import groovy.json.JsonOutput
import org.jdbi.v3.core.Jdbi
import org.jdbi.v3.core.statement.Query
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.sql.DataSource
import java.sql.SQLException

class QueryCommandService extends AbstractDbCommandService {

  private static final Logger LOG = LoggerFactory.getLogger(QueryCommandService)

  @Inject
  public ConsoleService consoleService
  @Inject
  public QueryService queryService

  @Inject
  QueryCommandService() {
  }

  void run(QueryCommand queryCommand) {
    queryCommand.with {
      def sqlArray = getSqlArray(queryCommand)
      def outputFormat = OutputFormat.fromCode(format)
      if (outputFormat == OutputFormat.JSON_PRETTY) {
        // Kept for one migration cycle: its success output is a bare array while
        // errors use the structured envelope, and -o does not capture a
        // validation error. Behaviour is unchanged on purpose.
        System.err.println(
            'Warning: -f json-pretty is deprecated and will be removed in the next major version. ' +
            'Use -f json --pretty instead.')
      }
      boolean structured = outputFormat in [OutputFormat.JSON, OutputFormat.MARKDOWN, OutputFormat.CSV]
      try {
        doRun(queryCommand) { dataSource ->
          def metadataLevel = MetadataLevel.fromString(metadata)
          LOG.debug('Output format: {}, Metadata level: {}', outputFormat, metadataLevel)
          if (structured) {
            executeWithAiFormat(dataSource, sqlArray, outputFormat, metadataLevel, outputFile, pretty)
          } else {
            QueryCallback<Void, Exception> callback = { Query query ->
              try {
                consoleService.renderResultSet(query, outputFormat, outputFile)
              } catch (SQLException e) {
                throw new RuntimeException(e)
              }
            }
            queryService.execute(dataSource, callback, sqlArray)
          }
        }
      } catch (CommandFailedException e) {
        // Already reported by executeWithAiFormat.
        throw e
      } catch (Exception e) {
        // Connection and driver failures are raised by doRun, outside the
        // callback, so they have to be reported here.
        if (structured) {
          def formatter = queryService.getFormatter(outputFormat.code)
          def payload = formatter.formatError(ErrorMapper.toValidationResult(e),
              sqlArray?.length == 1 ? sqlArray[0] : sqlArray?.join('; '))
          emit(payload, outputFile, outputFormat, pretty)
        }
        throw new CommandFailedException(e)
      }
    }
  }

  private void executeWithAiFormat(DataSource dataSource, String[] sqlArray, OutputFormat outputFormat, MetadataLevel metadataLevel, File outputFile, boolean pretty = false) throws Exception {
    if (sqlArray.length != 1) {
      throw new IllegalArgumentException('AI format only supports single SQL statement')
    }
    def sql = sqlArray[0]
    def formatter = queryService.getFormatter(outputFormat.code)
    try {
      def validationResult = queryService.validate(sql)
      if (!validationResult.valid) {
        emit(formatter.formatError(validationResult, sql), outputFile, outputFormat, pretty)
        // A known failure must exit non-zero regardless of format. Markdown and
        // CSV carry no success field, so the exit status is the only signal a
        // caller can rely on across formats.
        throw new CommandFailedException(
            new IllegalStateException("${validationResult.errorCode}: ${validationResult.message}"))
      }
      def result = collectQueryResult(dataSource, sql, metadataLevel)
      emit(formatter.formatSuccess(result, outputFormat, metadataLevel), outputFile, outputFormat, pretty)
    } catch (CommandFailedException e) {
      throw e
    } catch (Exception e) {
      // Connection and SQL failures used to escape as a stack trace with no
      // output at all; report them in the requested format instead.
      emit(formatter.formatError(ErrorMapper.toValidationResult(e), sql), outputFile, outputFormat, pretty)
      throw new CommandFailedException(e)
    }
  }

  /**
   * Writes the payload to the output file when given, otherwise to stdout.
   *
   * With --pretty on a JSON payload the whitespace changes but the structure
   * does not, so a caller parsing either form sees the same object.
   */
  private static void emit(String payload, File outputFile, OutputFormat format = null, boolean pretty = false) {
    String text = payload
    if (pretty && format == OutputFormat.JSON) {
      try {
        text = JsonOutput.prettyPrint(payload)
      } catch (Exception ignored) {
        // Leave the payload untouched rather than losing the result.
      }
    }
    if (outputFile != null) {
      outputFile.text = text
      return
    }
    System.out.println(text)
  }

  private QueryResult collectQueryResult(DataSource dataSource, String sql, MetadataLevel metadataLevel) {
    if (dataSource == null) {
      throw new IllegalStateException('DataSource is null. The driver may not be properly configured. Please check that the driver has associated JAR files or Maven dependencies.')
    }
    def jdbi = Jdbi.create(dataSource)
    jdbi.withHandle { handle ->
      def query = handle.createQuery(sql)
      def rowSet = query.mapToMap().list()
      def columnNames = []
      def columnTypes = []
      if (rowSet.size() > 0) {
        columnNames = rowSet[0].keySet() as List
      }
      def stmt = handle.connection.createStatement()
      def rs = stmt.executeQuery(sql)
      def metaData = rs.metaData
      for (int i = 1; i <= metaData.columnCount; i++) {
        if (columnNames.size() < i) {
          columnNames << metaData.getColumnLabel(i)
        }
        columnTypes << metaData.getColumnClassName(i)
      }
      rs.close()
      stmt.close()
      def rows = rowSet.collect { row -> columnNames.collect { colName -> row[colName] } }
      return new QueryResult(
          columns: columnNames,
          columnTypes: columnTypes,
          rows: rows,
          rowCount: rows.size(),
          columnCount: columnNames.size(),
          tableName: null,
          schema: null,
          database: null
      )
    } as QueryResult
  }

  protected String[] getSqlArray(QueryCommand queryCommand) {
    queryCommand.with {
      def sql = new ArrayList()
      if (execute) {
        sql.addAll(execute)
      }
      if (sqlText) {
        sql.add(sqlText)
      }
      return sql.toArray(new String[sql.size()])
    }
  }
}
