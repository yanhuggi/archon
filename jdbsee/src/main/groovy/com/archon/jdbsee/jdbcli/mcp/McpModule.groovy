package com.archon.jdbsee.jdbcli.mcp

import com.archon.jdbsee.jdbcli.QueryService
import com.archon.jdbsee.jdbcli.SchemaService
import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import com.archon.jdbsee.jdbcli.mcp.server.McpHttpServer
import com.archon.jdbsee.jdbcli.mcp.server.McpServer
import com.archon.jdbsee.jdbcli.mcp.server.McpSseTransport
import com.archon.jdbsee.jdbcli.mcp.tool.McpToolProviderImpl
import dagger.Module
import dagger.Provides

import javax.inject.Singleton

/**
 * Compatibility module retained for consumers that referenced the 2.1.1
 * artifact directly. The application itself uses the equivalent providers in
 * AppModule.
 */
@Module
class McpModule {

  @Provides
  @Singleton
  QueryResultCache queryResultCache() {
    new QueryResultCache()
  }

  @Provides
  @Singleton
  McpSseTransport mcpSseTransport() {
    new McpSseTransport()
  }

  @Provides
  @Singleton
  McpToolProviderImpl mcpToolProvider(
      QueryService queryService,
      SchemaService schemaService,
      QueryResultCache cache
  ) {
    new McpToolProviderImpl(queryService: queryService, schemaService: schemaService, cache: cache)
  }

  @Provides
  @Singleton
  McpServer mcpServer(McpToolProviderImpl toolProvider) {
    new McpServer(toolProvider: toolProvider)
  }

  @Provides
  @Singleton
  McpHttpServer mcpHttpServer(McpServer mcpServer, McpSseTransport transport) {
    new McpHttpServer(mcpServer: mcpServer, transport: transport)
  }
}
