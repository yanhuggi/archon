package com.archon.jdbsee.jdbcli;

import com.archon.jdbsee.jdbcli.mcp.McpCommand;
import picocli.CommandLine;

import javax.inject.Inject;

@CommandLine.Command(
    synopsisHeading = "@|bold NAME|@",
    customSynopsis = {"\n", "jdbsee - the SQL JDBC tool", "\n"},
    descriptionHeading = "@|bold DESCRIPTION|@",
    description = {
        "\n",
        "Jdbsee is a CLI tool providing both batch and interactive db access through JDBC drivers.\n",
        "Drivers can be downloaded on demand, automatically or explicitly loaded from local filesystem.\n",
        "Both drivers and database connection settings can be persisted for later reuse."
    },
    optionListHeading = "\n@|bold OPTIONS|@\n",
    commandListHeading = "\n@|bold COMMANDS|@\n",
    subcommands = {
        QueryCommand.class, ExecuteCommand.class, DriverCommand.class, AliasCommand.class,
        SchemaCommand.class, McpCommand.class, HelpCommand.class
    },
    versionProvider = PropertiesVersionProvider.class
)
public class MainCommand implements CliCommand {

  @CommandLine.Option(names = {"-h", "--help"}, usageHelp = true, description = "Display this help message")
  boolean help;

  @CommandLine.Option(names = {"-V", "--version"}, versionHelp = true, description = "Print version info")
  boolean versionRequested;

  @Inject
  public ConsoleService consoleService;

  private AppComponent appComponent;

  @Inject
  public MainCommand() {
  }

  @Override
  public void run() {
    consoleService.usage(this);
  }

  public AppComponent getAppComponent() {
    return appComponent;
  }

  public void setAppComponent(AppComponent appComponent) {
    this.appComponent = appComponent;
  }

  @Inject
  public ConsoleService getConsoleService() {
    return consoleService;
  }

  @Inject
  public void setConsoleService(ConsoleService consoleService) {
    this.consoleService = consoleService;
  }
}
