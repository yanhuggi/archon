package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.mcp.tool.McpToolProvider
import com.archon.jdbsee.jdbcli.model.McpRequest
import org.junit.Test

import static org.junit.Assert.*

/**
 * JSON-RPC dispatch must answer with an error object rather than throwing, so
 * that a malformed or hostile request cannot take the transport down.
 */
class McpServerDispatchTest {

  private static McpServer serverWith(McpToolProvider provider) {
    def server = new McpServer()
    server.toolProvider = provider
    return server
  }

  private static McpToolProvider provider(Closure listTools, Closure callTool) {
    return new McpToolProvider() {
      @Override
      List<Map<String, Object>> listTools() { listTools() }

      @Override
      Map<String, Object> callTool(String name, Map<String, Object> arguments) {
        callTool(name, arguments)
      }
    }
  }

  @Test
  void initializeReportsProtocolAndServerInfo() {
    def response = serverWith(null).handleRequest(new McpRequest(id: 1, method: 'initialize'))

    assertNull(response.error)
    assertEquals('2024-11-05', response.result.protocolVersion)
    assertEquals('jdbsee-mcp', response.result.serverInfo.name)
    assertEquals("the id must be echoed back", 1, response.id)
  }

  @Test
  void toolsListReturnsWhateverTheProviderExposes() {
    def server = serverWith(provider({ [[name: 'execute_query']] }, { n, a -> [:] }))

    def response = server.handleRequest(new McpRequest(id: 2, method: 'tools/list'))

    assertNull(response.error)
    assertEquals([[name: 'execute_query']], response.result.tools)
  }

  @Test
  void toolsCallPassesNameAndArgumentsThrough() {
    String seenName = null
    Map seenArgs = null
    def server = serverWith(provider({ [] }, { n, a ->
      seenName = n
      seenArgs = a
      return [success: true]
    }))

    def response = server.handleRequest(new McpRequest(id: 3, method: 'tools/call',
        params: [name: 'execute_query', arguments: [sql: 'SELECT 1']]))

    assertEquals('execute_query', seenName)
    assertEquals([sql: 'SELECT 1'], seenArgs)
    assertTrue(response.result.success)
  }

  @Test
  void unknownMethodBecomesMethodNotFound() {
    def response = serverWith(null).handleRequest(new McpRequest(id: 4, method: 'nonsense'))

    assertNotNull(response.error)
    assertEquals(-32601, response.error.code)
    assertEquals('nonsense', response.error.data.method)
  }

  @Test
  void aToolCallWithoutANameIsAnInvalidRequest() {
    def server = serverWith(provider({ [] }, { n, a -> [:] }))

    def response = server.handleRequest(new McpRequest(id: 5, method: 'tools/call', params: [:]))

    assertNotNull(response.error)
    assertEquals(-32600, response.error.code)
  }

  @Test
  void aThrowingToolBecomesAToolExecutionError() {
    def server = serverWith(provider({ [] }, { n, a ->
      throw new IllegalStateException('boom')
    }))

    def response = server.handleRequest(new McpRequest(id: 6, method: 'tools/call',
        params: [name: 'execute_query', arguments: [:]]))

    assertNotNull(response.error)
    assertEquals(-32000, response.error.code)
    assertEquals('boom', response.error.data.error)
  }

  /**
   * A missing method field must not throw: the switch has to treat it like any
   * other unrecognised method.
   */
  @Test
  void aRequestWithoutAMethodIsRejectedNotThrown() {
    def response = serverWith(null).handleRequest(new McpRequest(id: 7))

    assertNotNull(response.error)
    assertEquals(-32601, response.error.code)
  }

  /**
   * The provider is wired at startup, but a failure there must still surface as
   * a JSON-RPC error rather than escaping the dispatch loop.
   */
  @Test
  void aFailingProviderBecomesAnErrorResponseNotAnException() {
    def broken = provider({ throw new IllegalStateException('provider down') }, { n, a -> [:] })

    def response = serverWith(broken).handleRequest(new McpRequest(id: 8, method: 'tools/list'))

    assertNotNull("tools/list must not throw", response.error)
    assertEquals(-32603, response.error.code)
  }

  @Test
  void anUnconfiguredProviderBecomesAnErrorResponseNotAnException() {
    def response = serverWith(null).handleRequest(new McpRequest(id: 9, method: 'tools/list'))

    assertNotNull("a null provider must not throw", response.error)
    assertEquals(-32603, response.error.code)
  }
}
