package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import com.archon.jdbsee.jdbcli.exception.DuplicateNameException
import picocli.CommandLine

import javax.inject.Inject

import static picocli.CommandLine.*

@Command(name = "alias", description = "Provides persistent settings for db connections",
    subcommands = [
        AliasCreateCommand, AliasDeleteCommand,
        AliasListCommand, AliasShowCommand
    ]
)
class AliasCommand implements CliCommand {
  @ParentCommand
  private MainCommand mainCommand

  @Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
  boolean help

  @Inject//public for dagger
  public JdbsAliasDao jdbsAliasDao

  @Inject//public for dagger
  public ConsoleService consoleService

  @Inject
  AliasCommand(){}

  @Override
  void run () {
    consoleService.usage(this)
  }

  @Command(name = "create", description = "Create a new persistent alias")
  static class AliasCreateCommand implements Runnable {

    @ParentCommand
    private AliasCommand aliasCommand

    @Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
    boolean help

    @Parameters(index = "0", arity = "1", paramLabel = "DRIVER", description = "The name or id of the driver.")
    String driver

    @Parameters(index = "1", arity = "1", paramLabel = "NAME", description = "The name of the alias.")
    String name

    @Parameters(index = "2", paramLabel = "URL", description = "The JDBC url.")
    String url

    @Option(names = ["-u", "--user"], description = "The username")
    String username = ""

    @Option(names = ["-p", "--password"], description = "The password")
    String password = ""

    @Option(names = ["-P", "--ask-for-password"], description = "Prompt for password interactively")
    boolean askForPassword

    @Option(names = ["-r", "--remark"], description = "连接备注说明")
    String remark

    @Inject//dagger
    AliasCreateCommand (){}

    @Override
    void run () {
      String actualPassword = password
      if (askForPassword) {
        actualPassword = readPasswordInteractive()
      }
      try {
        aliasCommand.jdbsAliasDao.insert (driver, name, url, username, actualPassword, remark)
      } catch (DuplicateNameException e) {
        System.err.println("Error: ${e.message}")
        System.exit(1)
      }
    }

    protected String readPasswordInteractive () {
      def console = System.console()
      if (console == null) {
        throw new IllegalStateException("No console available for interactive password input")
      }
      new String(console.readPassword("Password: "))
    }
  }


  @Command(name = "delete", description = "Delete aliases")
  static class AliasDeleteCommand implements Runnable {
    @ParentCommand
    AliasCommand aliasCommand

    @Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
    boolean help

    @Parameters(index = "0..*", arity = "1", paramLabel = "ALIAS_ID", description = "The ID of the alias.")
    List<Long> aliasIds

    @Inject//dagger
    AliasDeleteCommand () {}

    @Override
    void run () {
      aliasCommand.jdbsAliasDao.delete (aliasIds as Long[])
    }
  }

  @Command(name = "list", description = "List registered aliases")
  static class AliasListCommand implements Runnable {
    @ParentCommand
    AliasCommand aliasCommand

    @Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
    boolean help

    @Inject
    public AliasService aliasService

    @Inject//dagger
    AliasListCommand () {}

    @Override
    void run () {
      aliasService.listAliases()
    }
  }

  @Command(name = "show", description = "Show details about alias settings")
  static class AliasShowCommand implements Runnable {
    @ParentCommand
    private AliasCommand aliasCommand

    @Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
    boolean help

    @Inject
    public AliasService aliasService

    @Parameters(index = "0", arity = "1", paramLabel = "ALIAS_ID", description = "The ID of the alias.")
    Long aliasId

    @Inject//dagger
    AliasShowCommand () {}

    @Override
    void run () {
      aliasService.showAlias(aliasId)
    }
  }
}
