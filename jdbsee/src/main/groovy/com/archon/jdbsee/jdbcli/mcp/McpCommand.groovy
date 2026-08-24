package com.archon.jdbsee.jdbcli.mcp

import com.archon.jdbsee.jdbcli.CliCommand
import com.archon.jdbsee.jdbcli.ConsoleService
import com.archon.jdbsee.jdbcli.DriverManagerFacade
import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import com.archon.jdbsee.jdbcli.mcp.server.McpHttpServer
import com.archon.jdbsee.jdbcli.mcp.tool.McpToolProviderImpl
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import picocli.CommandLine

import javax.inject.Inject

/**
 * `jdbsee mcp` - MCP (Model Context Protocol) HTTP server commands.
 *
 * Exposes the CLI as a JSON-RPC over SSE server so that AI agents can
 * query the database, explore the schema and retrieve cached results.
 */
@CommandLine.Command(name = "mcp", description = "MCP server commands", subcommands = [StartCommand])
class McpCommand implements CliCommand {

  @Inject
  public ConsoleService consoleService

  @CommandLine.Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
  boolean help

  @Inject
  McpCommand() {}

  @Override
  void run() {
    consoleService.usage(this)
  }

  /**
   * `jdbsee mcp start` - boot the HTTP SSE server on the configured port.
   */
  @CommandLine.Command(name = "start", description = "Start MCP server")
  static class StartCommand implements Runnable {
    private static final Logger LOG = LoggerFactory.getLogger(StartCommand)

    @CommandLine.Option(names = ["-a", "--alias"], description = "默认数据库别名（可选）")
    String defaultAlias

    @CommandLine.Option(names = ["--port"], description = "HTTP 服务器端口（默认: 8080）")
    int port = 8080

    @CommandLine.Option(names = ["--bind"],
        description = "监听地址（默认: 127.0.0.1）。绑定非回环地址会使数据库可从网络访问")
    String bind = "127.0.0.1"

    @CommandLine.Option(names = ["--auth-token"],
        description = "访问 /mcp 所需的 bearer token；未指定时自动生成并打印")
    String authToken

    @CommandLine.Option(names = ["--timeout"], description = "结果缓存 TTL（秒），默认: 300")
    int timeout = 300

    @CommandLine.Option(names = ["--max-results"], description = "单次查询最大行数（默认: 10000）")
    int maxResults = 10000

    @CommandLine.Option(names = ["-h", "--help"], usageHelp = true, description = "Display this help message")
    boolean help

    @Inject
    public McpHttpServer mcpServer

    @Inject
    public McpToolProviderImpl toolProvider

    @Inject
    public DriverManagerFacade driverManagerFacade

    @Inject
    public JdbsAliasDao jdbsAliasDao

    private JdbiCache jdbiCache

    @Inject
    StartCommand() {}

    @Override
    void run() {
      try {
        toolProvider.configureLimits(timeout, maxResults)
        jdbiCache = new JdbiCache(jdbsAliasDao, driverManagerFacade)
        if (defaultAlias) {
          LOG.info("Preloading default alias: {}", defaultAlias)
          jdbiCache.getJdbi(defaultAlias)
        }
        toolProvider.jdbiCache = jdbiCache
        toolProvider.defaultAlias = defaultAlias
        toolProvider.aliasDao = jdbsAliasDao
        toolProvider.init()
        mcpServer.port = port
        mcpServer.bindHost = bind
        mcpServer.authToken = authToken
        String effectiveToken = mcpServer.ensureAuthToken()
        mcpServer.start()
        Runtime.getRuntime().addShutdownHook(new Thread({
          LOG.info("Shutting down MCP server...")
          jdbiCache?.close()
          LOG.info("MCP server stopped")
        }))
        println("MCP Server started on ${bind}:${port}")
        if (!mcpServer.loopbackBind) {
          println("警告: 绑定在 ${bind} 上，数据库可从网络访问")
        }
        if (!authToken) {
          println("Auth token (自动生成): ${effectiveToken}")
        } else {
          println("Auth token: 使用 --auth-token 指定的值")
        }
        println("请求需携带请求头: Authorization: Bearer <token>")
        if (defaultAlias) {
          println("Default alias: ${defaultAlias}")
        } else {
          println("No default alias configured (must specify alias in tool calls)")
        }
        println("Press Ctrl+C to stop...")
        while (true) {
          Thread.sleep(1000)
        }
      } catch (InterruptedException e) {
        println("\nMCP Server stopped")
      } catch (Exception e) {
        System.err.println("Error starting MCP server: ${e.message}")
        e.printStackTrace(System.err)
        System.exit(1)
      }
    }
  }
}
