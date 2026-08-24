package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.OutputFormat
import picocli.CommandLine

import javax.inject.Inject

@CommandLine.Command(
    name = 'query',
    description = 'Executes queries passing on-the-fly the db settings (driver, username)',
    showDefaultValues = true
)
class QueryCommand extends AbstractDbCommand {

  @Inject
  public QueryCommandService queryCommandService

  @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
  boolean help

  @CommandLine.Parameters(arity = '0..1', index = '0..*', paramLabel = 'QUERY', description = 'The SQL to run.')
  String sqlText

  @CommandLine.Option(names = ['-x', '--execute'], description = 'Additional SQL commands to be executed before the specified QUERY')
  List<String> execute

  @CommandLine.Option(paramLabel = 'FORMAT', names = ['-f', '--format'], description = 'Output format: json|markdown|csv (default: json)')
  String format = 'json'

  @CommandLine.Option(paramLabel = 'LEVEL', names = ['--metadata'], description = 'Metadata level: basic|enhanced|full (default: basic)')
  String metadata = 'basic'

  @CommandLine.Option(names = ['--pretty'],
      description = 'Indent JSON output for human reading. Only affects -f json; the structure is unchanged')
  boolean pretty

  @CommandLine.Option(paramLabel = 'OUTPUT_FILE', names = ['-o', '--output-file'], description = 'File to use for saving output')
  File outputFile

  @Inject
  QueryCommand() {
  }

  OutputFormat getOutputType() {
    OutputFormat.fromCode(format)
  }

  @Override
  void run() {
    queryCommandService.run(this)
  }
}
