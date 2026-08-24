package com.archon.jdbsee.jdbcli

import picocli.CommandLine

import javax.inject.Inject

@CommandLine.Command(
    name = 'execute',
    description = 'Execute DDL/DML statements (INSERT/UPDATE/DELETE/CREATE/ALTER/DROP). Supports multiple semicolon-separated statements or file input.',
    showDefaultValues = true
)
class ExecuteCommand extends AbstractDbCommand {

  @Inject
  public ExecuteCommandService executeCommandService

  @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
  boolean help

  @CommandLine.Parameters(arity = '0..1', paramLabel = 'SQL', description = 'The SQL statement(s) to execute. Multiple statements separated by semicolons.')
  String sqlText

  @CommandLine.Option(paramLabel = 'SQL_FILE', names = ['--sql-file'], description = 'File containing SQL statements to execute')
  File sqlFile

  @CommandLine.Option(paramLabel = 'FORMAT', names = ['-f', '--format'], description = 'Output format: json|markdown|csv (default: json)')
  String format = 'json'

  @CommandLine.Option(paramLabel = 'OUTPUT_FILE', names = ['-o', '--output-file'], description = 'File to use for saving output')
  File outputFile

  @Inject
  ExecuteCommand() {
  }

  @Override
  void run() {
    executeCommandService.run(this)
  }
}
