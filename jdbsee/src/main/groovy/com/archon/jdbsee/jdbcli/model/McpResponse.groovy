package com.archon.jdbsee.jdbcli.model

import groovy.transform.EqualsAndHashCode
import groovy.transform.ToString

/**
 * JSON-RPC 2.0 response for the MCP HTTP server.
 */
@ToString
@EqualsAndHashCode
class McpResponse {
    String jsonrpc = "2.0"
    Object id
    Map<String, Object> result
    McpError error

    McpResponse(String jsonrpc, Object id, Map<String, Object> result, McpError error) {
        this.jsonrpc = jsonrpc
        this.id = id
        this.result = result
        this.error = error
    }

    McpResponse(String jsonrpc, Object id, Map<String, Object> result) {
        this(jsonrpc, id, result, null)
    }

    McpResponse(String jsonrpc, Object id) {
        this(jsonrpc, id, null, null)
    }

    McpResponse(String jsonrpc) {
        this(jsonrpc, null, null, null)
    }

    McpResponse() {
        this("2.0", null, null, null)
    }
}
