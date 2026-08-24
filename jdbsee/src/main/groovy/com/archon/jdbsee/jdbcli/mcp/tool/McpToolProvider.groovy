package com.archon.jdbsee.jdbcli.mcp.tool

interface McpToolProvider {
  List<Map<String, Object>> listTools()
  Map<String, Object> callTool(String toolName, Map<String, Object> arguments)
}
