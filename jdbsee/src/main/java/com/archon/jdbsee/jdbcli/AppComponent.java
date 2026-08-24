package com.archon.jdbsee.jdbcli;

import com.archon.jdbsee.jdbcli.config.JdbsAliasDao;
import com.archon.jdbsee.jdbcli.format.FormatterFactory;
import com.archon.jdbsee.jdbcli.mcp.McpCommand;
import com.archon.jdbsee.jdbcli.mcp.server.McpHttpServer;
import com.archon.jdbsee.jdbcli.mcp.server.McpServer;
import com.archon.jdbsee.jdbcli.mcp.server.McpSseTransport;
import com.archon.jdbsee.jdbcli.security.AliasPasswordMigrationService;
import com.archon.jdbsee.jdbcli.security.SqlSecurityValidator;
import dagger.Component;

import javax.inject.Singleton;

@Singleton
@Component(modules = {AppModule.class})
interface AppComponent {

  MainCommand getMainCommand();

  void inject(PropertiesVersionProvider versionProvider);

  void inject(ConsoleService consoleService);

  void inject(SqlSecurityValidator validator);

  void inject(FormatterFactory formatterFactory);

  void inject(QueryService queryService);

  void inject(AsciiTableResultSetScanner ascii);

  void inject(HelpCommand command);

  void inject(DriverCommand command);

  void inject(DriverCommand.DriverCreateCommand command);

  void inject(DriverCommand.DriverDeleteCommand command);

  void inject(DriverCommand.DriverListCommand command);

  void inject(DriverCommand.DriverShowCommand command);

  void inject(DriverCommand.JarAddCommand command);

  void inject(DriverCommand.JarRemoveCommand command);

  void inject(DriverCommand.DependencyAddCommand command);

  void inject(DriverCommand.DependencyRemoveCommand command);

  void inject(AliasCommand command);

  void inject(AliasCommand.AliasCreateCommand command);

  void inject(AliasCommand.AliasDeleteCommand command);

  void inject(AliasCommand.AliasListCommand command);

  void inject(AliasCommand.AliasShowCommand command);

  void inject(SchemaCommand command);

  void inject(SchemaCommand.SchemaTablesCommand command);

  void inject(SchemaCommand.SchemaViewsCommand command);

  void inject(SchemaCommand.SchemaDriverCommand command);

  void inject(QueryCommand command);

  void inject(ExecuteCommand command);

  void inject(McpCommand command);

  void inject(McpCommand.StartCommand command);

  void inject(McpServer server);

  void inject(McpSseTransport transport);

  void inject(McpHttpServer httpServer);

  void inject(JdbsAliasDao dao);

  AliasPasswordMigrationService getAliasPasswordMigrationService();
}
