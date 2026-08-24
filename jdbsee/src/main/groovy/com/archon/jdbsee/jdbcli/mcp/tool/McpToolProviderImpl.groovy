package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.QueryService
import com.archon.jdbsee.jdbcli.SchemaService
import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton
import javax.sql.DataSource

/**
 * Default {@link McpToolProvider}. Assembles the concrete tools and
 * exposes their JSON Schema definitions to the MCP client.
 */
@Singleton
class McpToolProviderImpl implements McpToolProvider {
  private static final Logger LOG = LoggerFactory.getLogger(McpToolProviderImpl)

  @Inject
  QueryService queryService

  @Inject
  SchemaService schemaService

  @Inject
  QueryResultCache cache

  DataSource dataSource
  JdbiCache jdbiCache
  String defaultAlias
  JdbsAliasDao aliasDao
  private int maxResults = 10000

  private QueryTool queryTool
  private SchemaTool schemaTool
  private ResultTool resultTool
  private ListAliasesTool listAliasesTool

  @Inject
  McpToolProviderImpl() {}

  void setQueryService(QueryService queryService) { this.queryService = queryService }
  void setSchemaService(SchemaService schemaService) { this.schemaService = schemaService }
  void setCache(QueryResultCache cache) { this.cache = cache }
  void setDataSource(DataSource dataSource) { this.dataSource = dataSource }
  void setJdbiCache(JdbiCache jdbiCache) { this.jdbiCache = jdbiCache }
  void setDefaultAlias(String defaultAlias) { this.defaultAlias = defaultAlias }
  void setAliasDao(JdbsAliasDao aliasDao) { this.aliasDao = aliasDao }

  void configureLimits(int timeoutSeconds, int maxResults) {
    if (timeoutSeconds <= 0) {
      throw new IllegalArgumentException('--timeout must be greater than zero')
    }
    if (maxResults <= 0) {
      throw new IllegalArgumentException('--max-results must be greater than zero')
    }
    cache.timeoutSeconds = timeoutSeconds
    this.maxResults = maxResults
  }

  /**
   * Wires the injected dependencies into the concrete tools.
   * Must be called once before the provider is used.
   */
  void init() {
    queryTool = new QueryTool(
        queryService: queryService,
        cache: cache,
        jdbiCache: jdbiCache,
        defaultAlias: defaultAlias,
        maxResults: maxResults
    )
    schemaTool = new SchemaTool(
        schemaService: schemaService,
        jdbiCache: jdbiCache,
        defaultAlias: defaultAlias
    )
    resultTool = new ResultTool(cache: cache, defaultAlias: defaultAlias)
    listAliasesTool = new ListAliasesTool()
    listAliasesTool.aliasDao = aliasDao
    listAliasesTool.defaultAlias = defaultAlias
  }

  @Override
  List<Map<String, Object>> listTools() {
    [
        executeQueryToolSchema(),
        listTablesToolSchema(),
        describeTableToolSchema(),
        getResultToolSchema(),
        listAliasesToolSchema()
    ]
  }

  @Override
  Map<String, Object> callTool(String toolName, Map<String, Object> arguments) {
    switch (toolName) {
      case "execute_query":
        return queryTool.execute(arguments)
      case "list_tables":
        return schemaTool.execute(arguments + [action: "list_tables"])
      case "describe_table":
        return schemaTool.execute(arguments + [action: "describe_table"])
      case "get_result":
        return resultTool.execute(arguments)
      case "list_aliases":
        return listAliasesTool.execute(arguments)
      default:
        throw new IllegalArgumentException("Unknown tool: ${toolName}")
    }
  }

  private Map<String, Object> executeQueryToolSchema() {
    return [
        name       : "execute_query",
        description: "执行 SQL SELECT 查询，返回结果 ID 和元数据",
        inputSchema: [
            type      : "object",
            properties: [
                sql            : [type: "string", description: "SQL SELECT 查询语句"],
                alias          : [type: "string", description: "数据库别名（可选，未指定则使用默认别名）"],
                format         : [type: "string", enum: ["json", "markdown", "csv"], description: "输出格式", default: "json"],
                metadata_level : [type: "string", enum: ["basic", "enhanced", "full"], description: "元数据详细程度", default: "basic"]
            ],
            required  : ["sql"]
        ]
    ]
  }

  private Map<String, Object> listTablesToolSchema() {
    return [
        name       : "list_tables",
        description: "列出数据库中的所有表",
        inputSchema: [
            type      : "object",
            properties: [
                alias  : [type: "string", description: "数据库别名（可选）"],
                pattern: [type: "string", description: "表名过滤模式（如 'user_%'）", default: "%"]
            ]
        ]
    ]
  }

  private Map<String, Object> describeTableToolSchema() {
    return [
        name       : "describe_table",
        description: "获取表的列信息、索引、约束等",
        inputSchema: [
            type      : "object",
            properties: [
                alias     : [type: "string", description: "数据库别名（可选）"],
                table_name: [type: "string", description: "表名"]
            ],
            required  : ["table_name"]
        ]
    ]
  }

  private Map<String, Object> getResultToolSchema() {
    return [
        name       : "get_result",
        description: "通过结果 ID 获取缓存的查询结果数据",
        inputSchema: [
            type      : "object",
            properties: [
                result_id: [type: "string", description: "查询返回的结果 ID"],
                offset   : [type: "integer", description: "起始行偏移量", default: 0],
                limit    : [type: "integer", description: "返回行数限制", default: 100]
            ],
            required  : ["result_id"]
        ]
    ]
  }

  private Map<String, Object> listAliasesToolSchema() {
    return [
        name       : "list_aliases",
        description: "列出所有可用的数据库别名",
        inputSchema: [
            type      : "object",
            properties: [:],
            required  : []
        ]
    ]
  }
}
