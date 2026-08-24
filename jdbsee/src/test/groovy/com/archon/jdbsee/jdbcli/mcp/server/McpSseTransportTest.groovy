package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.model.McpError
import com.archon.jdbsee.jdbcli.model.McpResponse
import groovy.json.JsonSlurper
import org.junit.Test

import static org.junit.Assert.*

/**
 * SSE framing has to stay exact: a client splits events on the blank line and
 * reads the payload from the "data:" prefix.
 */
class McpSseTransportTest {

  private final McpSseTransport transport = new McpSseTransport()

  @Test
  void encodesAResponseAsASingleSseEvent() {
    def event = transport.toSseEvent(new McpResponse(id: 1, result: [status: 'ok']))

    assertTrue("must name the event type", event.startsWith('event: message\n'))
    assertTrue("must terminate the event with a blank line", event.endsWith('\n\n'))

    def payload = event.readLines().find { it.startsWith('data: ') }.substring(6)
    def parsed = new JsonSlurper().parseText(payload)
    assertEquals('ok', parsed.result.status)
    assertEquals(1, parsed.id)
  }

  @Test
  void encodesAnErrorResponseWithItsCode() {
    def event = transport.toSseEvent(new McpResponse(
        id: 2, error: new McpError(code: -32601, message: 'Method not found')))

    def payload = event.readLines().find { it.startsWith('data: ') }.substring(6)
    def parsed = new JsonSlurper().parseText(payload)

    assertEquals(-32601, parsed.error.code)
    assertEquals('Method not found', parsed.error.message)
  }

  @Test
  void heartbeatIsItsOwnEventTypeSoClientsCanIgnoreIt() {
    def beat = transport.createHeartbeat()

    assertTrue(beat.startsWith('event: heartbeat\n'))
    assertTrue(beat.endsWith('\n\n'))

    def payload = beat.readLines().find { it.startsWith('data: ') }.substring(6)
    assertTrue(new JsonSlurper().parseText(payload).timestamp > 0)
  }

  @Test
  void parsesAWellFormedJsonRpcRequest() {
    def request = transport.parseRequest(
        '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"execute_query"}}')

    assertEquals('tools/call', request.method)
    assertEquals('execute_query', request.params.name)
  }

  @Test
  void rejectsMalformedJsonWithAClearMessage() {
    ['{"jsonrpc":', 'not json at all', '{"a":'].each { body ->
      try {
        transport.parseRequest(body)
        fail("should have rejected: ${body}")
      } catch (IllegalArgumentException e) {
        assertTrue(e.message.contains('Invalid JSON-RPC request'))
      }
    }
  }

  /**
   * A payload that parses but carries no method must come back as a request
   * object so dispatch can answer with method-not-found, rather than throwing
   * here.
   */
  @Test
  void acceptsSyntacticallyValidJsonWithoutAMethod()  {
    def request = transport.parseRequest('{"jsonrpc":"2.0","id":1}')

    assertNotNull(request)
    assertNull(request.method)
  }

  @Test
  void payloadNewlinesDoNotBreakEventFraming() {
    // A driver message containing a newline must not split the SSE event.
    def event = transport.toSseEvent(new McpResponse(
        id: 3, error: new McpError(code: -32000, message: "line1\nline2")))

    def dataLines = event.readLines().findAll { it.startsWith('data: ') }
    assertEquals("the payload must stay on one data line", 1, dataLines.size())
    assertEquals('line1\nline2',
        new JsonSlurper().parseText(dataLines[0].substring(6)).error.message)
  }
}
