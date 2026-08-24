package com.archon.jdbsee.jdbcli

import picocli.CommandLine

import javax.inject.Inject

@CommandLine.Command(
    name = 'schema',
    description = 'Provides info about database and driver',
    subcommands = [SchemaTablesCommand, SchemaViewsCommand, SchemaDriverCommand]
)
class SchemaCommand implements CliCommand {

  @CommandLine.ParentCommand
  private MainCommand mainCommand

  @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
  boolean help

  @Inject
  public SchemaService service
  @Inject
  public ConsoleService consoleService

  @Inject
  SchemaCommand() {
  }

  @Override
  void run() {
    consoleService.usage(this)
  }

  @CommandLine.Command(name = 'tables', description = 'List all tables')
  static class SchemaTablesCommand extends AbstractDbCommand {
    @CommandLine.ParentCommand
    private SchemaCommand parentCommand

    @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
    boolean help

    @CommandLine.Parameters(arity = '0..1', index = '0', paramLabel = 'PATTERN', description = 'A table name pattern; when specified must match the table name as it is stored in the database.')
    String matches

    @Inject
    SchemaTablesCommand() {
    }

    @Override
    void run() {
      parentCommand.service.run(this)
    }
  }

  @CommandLine.Command(name = 'views', description = 'List all views')
  static class SchemaViewsCommand extends AbstractDbCommand {
    @CommandLine.ParentCommand
    private SchemaCommand parentCommand

    @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
    boolean help

    @CommandLine.Parameters(arity = '0..1', index = '0', paramLabel = 'PATTERN', description = 'A table name pattern; when specified must match the table name as it is stored in the database.')
    String matches

    @Inject
    SchemaViewsCommand() {
    }

    @Override
    void run() {
      parentCommand.service.run(this)
    }
  }

  @CommandLine.Command(name = 'driver', description = 'Show driver info')
  static class SchemaDriverCommand extends AbstractDbCommand {
    @CommandLine.ParentCommand
    SchemaCommand parentCommand

    @CommandLine.Option(names = ['-h', '--help'], usageHelp = true, description = 'Display this help message')
    boolean help

    @CommandLine.Parameters(arity = '0..1', index = '0', paramLabel = 'PATTERN', description = 'A property name pattern; when specified must match the proeprty name as it is exposed by the database metadata.')
    String matches

    @Inject
    SchemaDriverCommand() {
    }

    @Override
    void run() {
      parentCommand.service.run(this)
    }
  }
}
