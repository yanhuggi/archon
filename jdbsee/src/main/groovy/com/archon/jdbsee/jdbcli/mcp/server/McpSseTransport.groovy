package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.model.McpRequest
import com.archon.jdbsee.jdbcli.model.McpResponse
import com.google.gson.Gson

/**
 * Encodes {@link McpResponse} objects as Server-Sent Events and decodes
 * inbound JSON-RPC payloads into {@link McpRequest} objects.
 */
class McpSseTransport {
  private final Gson gson = new Gson()

  String toSseEvent(McpResponse response) {
    String json = gson.toJson(response)
    return "event: message\ndata: ${json}\n\n"
  }

  String createHeartbeat() {
    def heartbeat = [timestamp: System.currentTimeMillis()]
    String json = gson.toJson(heartbeat)
    return "event: heartbeat\ndata: ${json}\n\n"
  }

  McpRequest parseRequest(String requestBody) {
    try {
      return gson.fromJson(requestBody, McpRequest)
    } catch (Exception e) {
      throw new IllegalArgumentException("Invalid JSON-RPC request: ${e.message}", e)
    }
  }
}
