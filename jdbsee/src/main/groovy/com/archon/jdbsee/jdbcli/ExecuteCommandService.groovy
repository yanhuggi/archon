package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.exception.CommandFailedException
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject

class ExecuteCommandService extends AbstractDbCommandService {

  private static final Logger LOG = LoggerFactory.getLogger(ExecuteCommandService)

  @Inject
  public ExecuteService executeService
  @Inject
  public ConsoleService consoleService

  @Inject
  ExecuteCommandService() {
  }

  void run(ExecuteCommand executeCommand) {
    executeCommand.with {
      def resolvedSql = resolveSqlText(sqlText, sqlFile)
      if (!resolvedSql?.trim()) {
        System.err.println('Error: SQL statement(s) required. Provide SQL as argument or via --sql-file')
        return
      }
      try {
        boolean failed = false
        doRun(executeCommand) { dataSource ->
          def outcome = executeService.executeBatchWithOutcome(dataSource, resolvedSql, format)
          emit(outcome.output, outputFile)
          failed = outcome.failed
        }
        if (failed) {
          // The formatted result already carries the error; signal the failure
          // through the exit status too.
          throw new CommandFailedException(new IllegalStateException('execute reported a failure'))
        }
      } catch (CommandFailedException e) {
        throw e
      } catch (Exception e) {
        // A connection failure happens outside the callback, so report it here
        // rather than letting it escape as a stack trace with no output.
        emit(executeService.formatFailure(ErrorMapper.toValidationResult(e), resolvedSql, format), outputFile)
        throw new CommandFailedException(e)
      }
    }
  }

  private static void emit(String payload, File outputFile) {
    if (outputFile != null) {
      outputFile.text = payload
      return
    }
    System.out.println(payload)
  }

  private String resolveSqlText(String sqlText, File sqlFile) {
    if (sqlFile != null && sqlText != null) {
      System.err.println('Warning: Both SQL argument and --sql-file provided; using --sql-file')
    }
    if (sqlFile != null) {
      if (!sqlFile.exists()) {
        System.err.println("Error: SQL file not found: ${sqlFile.absolutePath}")
        return null
      }
      if (!sqlFile.canRead()) {
        System.err.println("Error: Cannot read SQL file: ${sqlFile.absolutePath}")
        return null
      }
      return sqlFile.text
    }
    return sqlText
  }
}
