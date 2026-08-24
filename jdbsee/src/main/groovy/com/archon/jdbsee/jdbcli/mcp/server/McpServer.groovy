package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.mcp.tool.McpToolProvider
import com.archon.jdbsee.jdbcli.model.McpError
import com.archon.jdbsee.jdbcli.model.McpRequest
import com.archon.jdbsee.jdbcli.model.McpResponse
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton

/**
 * Dispatches JSON-RPC requests to the appropriate MCP handler.
 *
 * Supported methods: "initialize", "tools/list" and "tools/call".
 * Errors follow the JSON-RPC 2.0 code conventions.
 */
@Singleton
class McpServer {
  private static final Logger LOG = LoggerFactory.getLogger(McpServer)

  McpToolProvider toolProvider

  @Inject
  McpServer() {}

  void setToolProvider(McpToolProvider toolProvider) {
    this.toolProvider = toolProvider
  }

  McpResponse handleRequest(McpRequest request) {
    if (request == null) {
      return new McpResponse(
          id: null,
          error: new McpError(code: -32600, message: "Invalid Request", data: [details: "Missing request"])
      )
    }
    // Every dispatch path is guarded: a handler must never throw out of here,
    // or the transport loses the JSON-RPC error envelope for that request.
    try {
      switch (request.method) {
        case "initialize":
          return handleInitialize(request)
        case "tools/list":
          return handleToolsList(request)
        case "tools/call":
          return handleToolCall(request)
        default:
          return methodNotFound(request)
      }
    } catch (Exception e) {
      LOG.error("Error handling request", e)
      return internalError(request, e)
    }
  }

  private McpResponse handleInitialize(McpRequest request) {
    def capabilities = [tools: [:]]
    def result = [
        protocolVersion: "2024-11-05",
        capabilities   : capabilities,
        serverInfo     : [name: "jdbsee-mcp", version: "2.0.0"]
    ]
    return new McpResponse(id: request.id, result: result)
  }

  private McpResponse handleToolsList(McpRequest request) {
    return new McpResponse(id: request.id, result: [tools: toolProvider.listTools()])
  }

  private McpResponse handleToolCall(McpRequest request) {
    String toolName = request.params?.name as String
    Map arguments = request.params?.arguments as Map
    if (!toolName) {
      return invalidRequest(request, "Missing tool name")
    }
    try {
      def result = toolProvider.callTool(toolName, arguments)
      return new McpResponse(id: request.id, result: result)
    } catch (Exception e) {
      LOG.error("Tool execution error: {}", toolName, e)
      return toolExecutionError(request, e)
    }
  }

  private McpResponse methodNotFound(McpRequest request) {
    return new McpResponse(
        id: request.id,
        error: new McpError(code: -32601, message: "Method not found", data: [method: request.method])
    )
  }

  private McpResponse invalidRequest(McpRequest request, String message) {
    return new McpResponse(
        id: request.id,
        error: new McpError(code: -32600, message: "Invalid Request", data: [details: message])
    )
  }

  private McpResponse toolExecutionError(McpRequest request, Exception e) {
    return new McpResponse(
        id: request.id,
        error: new McpError(code: -32000, message: "Tool execution error", data: [error: e.message])
    )
  }

  private McpResponse internalError(McpRequest request, Exception e) {
    return new McpResponse(
        id: request.id,
        error: new McpError(code: -32603, message: "Internal error", data: [error: e.message])
    )
  }
}
