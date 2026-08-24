package com.archon.jdbsee.jdbcli.model

import groovy.transform.EqualsAndHashCode
import groovy.transform.ToString

/**
 * JSON-RPC 2.0 error object.
 */
@ToString
@EqualsAndHashCode
class McpError {
    int code
    String message
    Map<String, Object> data

    McpError(int code, String message, Map<String, Object> data) {
        this.code = code
        this.message = message
        this.data = data
    }

    McpError(int code, String message) {
        this(code, message, null)
    }

    McpError(int code) {
        this(code, null, null)
    }

    McpError() {
        this(0, null, null)
    }
}
