package com.archon.jdbsee.jdbcli.model

import groovy.transform.EqualsAndHashCode
import groovy.transform.ToString

/**
 * JSON-RPC 2.0 request for the MCP HTTP server.
 */
@ToString
@EqualsAndHashCode
class McpRequest {
    String jsonrpc = "2.0"
    Object id
    String method
    Map<String, Object> params

    McpRequest(String jsonrpc, Object id, String method, Map<String, Object> params) {
        this.jsonrpc = jsonrpc
        this.id = id
        this.method = method
        this.params = params
    }

    McpRequest(String jsonrpc, Object id, String method) {
        this(jsonrpc, id, method, null)
    }

    McpRequest(String jsonrpc, Object id) {
        this(jsonrpc, id, null, null)
    }

    McpRequest(String jsonrpc) {
        this(jsonrpc, null, null, null)
    }

    McpRequest() {
        this("2.0", null, null, null)
    }
}
