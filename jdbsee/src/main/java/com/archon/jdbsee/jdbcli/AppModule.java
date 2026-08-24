package com.archon.jdbsee.jdbcli;

import com.archon.jdbsee.jdbcli.format.CsvAiResponseFormatter;
import com.archon.jdbsee.jdbcli.format.FormatterFactory;
import com.archon.jdbsee.jdbcli.format.JsonAiResponseFormatter;
import com.archon.jdbsee.jdbcli.format.MarkdownAiResponseFormatter;
import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache;
import com.archon.jdbsee.jdbcli.mcp.server.McpHttpServer;
import com.archon.jdbsee.jdbcli.mcp.server.McpServer;
import com.archon.jdbsee.jdbcli.mcp.server.McpSseTransport;
import com.archon.jdbsee.jdbcli.mcp.tool.McpToolProviderImpl;
import com.archon.jdbsee.jdbcli.security.AliasPasswordMigrationService;
import com.archon.jdbsee.jdbcli.security.CryptoService;
import com.archon.jdbsee.jdbcli.security.DmlSecurityValidator;
import com.archon.jdbsee.jdbcli.security.SqlSecurityValidator;
import dagger.Module;
import dagger.Provides;

import javax.inject.Singleton;

@Module
class AppModule {

  @Provides
  MainCommand mainCommand() {
    return new MainCommand();
  }

  @Provides
  @Singleton
  SqlSecurityValidator sqlSecurityValidator() {
    return new SqlSecurityValidator();
  }

  @Provides
  @Singleton
  JsonAiResponseFormatter jsonAiResponseFormatter() {
    return new JsonAiResponseFormatter();
  }

  @Provides
  @Singleton
  MarkdownAiResponseFormatter markdownAiResponseFormatter() {
    return new MarkdownAiResponseFormatter();
  }

  @Provides
  @Singleton
  CsvAiResponseFormatter csvAiResponseFormatter() {
    return new CsvAiResponseFormatter();
  }

  @Provides
  @Singleton
  QueryService queryService(SqlSecurityValidator securityValidator, FormatterFactory formatterFactory) {
    return new QueryService(securityValidator, formatterFactory);
  }

  @Provides
  @Singleton
  DmlSecurityValidator dmlSecurityValidator() {
    return new DmlSecurityValidator();
  }

  @Provides
  @Singleton
  CryptoService cryptoService(ConfigService configService) {
    return new CryptoService(configService);
  }

  @Provides
  @Singleton
  AliasPasswordMigrationService aliasPasswordMigrationService(SettingsService settingsService, CryptoService cryptoService) {
    return new AliasPasswordMigrationService(settingsService, cryptoService);
  }

  @Provides
  @Singleton
  ExecuteService executeService(DmlSecurityValidator dmlSecurityValidator, FormatterFactory formatterFactory) {
    return new ExecuteService(dmlSecurityValidator, formatterFactory);
  }

  @Provides
  @Singleton
  DriverManagerFacade driverManagerFacade() {
    return new DriverManagerFacade();
  }

  @Provides
  @Singleton
  QueryResultCache queryResultCache() {
    return new QueryResultCache();
  }

  @Provides
  @Singleton
  McpSseTransport mcpSseTransport() {
    return new McpSseTransport();
  }

  @Provides
  @Singleton
  McpServer mcpServer(QueryResultCache cache, McpToolProviderImpl toolProvider) {
    McpServer server = new McpServer();
    server.setToolProvider(toolProvider);
    return server;
  }

  @Provides
  @Singleton
  McpToolProviderImpl mcpToolProvider(QueryService queryService, SchemaService schemaService, QueryResultCache cache) {
    McpToolProviderImpl provider = new McpToolProviderImpl();
    provider.setQueryService(queryService);
    provider.setSchemaService(schemaService);
    provider.setCache(cache);
    return provider;
  }

  @Provides
  @Singleton
  McpHttpServer mcpHttpServer(McpServer mcpServer, McpSseTransport transport) {
    McpHttpServer server = new McpHttpServer();
    server.setMcpServer(mcpServer);
    server.setTransport(transport);
    return server;
  }
}
