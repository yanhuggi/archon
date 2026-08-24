package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.model.McpRequest
import com.archon.jdbsee.jdbcli.model.McpResponse
import org.junit.After
import org.junit.Test

import static org.junit.Assert.*

/**
 * The /mcp endpoint exposes queries under the server's stored database
 * credentials, so it must bind to loopback and reject unauthenticated calls.
 */
class McpHttpServerAuthTest {

  private McpHttpServer server

  @After
  void stopServer() {
    server?.stop()
  }

  @Test
  void defaultsToLoopbackAndGeneratesAToken() {
    server = newServer()

    assertEquals('127.0.0.1', server.bindHost)
    assertTrue(server.loopbackBind)

    String token = server.ensureAuthToken()
    assertNotNull(token)
    assertTrue("token should be long enough to resist guessing", token.length() >= 32)
    assertEquals("token must be stable once generated", token, server.ensureAuthToken())
  }

  @Test
  void nonLoopbackBindIsReportedAsSuch() {
    server = newServer()
    server.bindHost = '0.0.0.0'

    assertFalse("0.0.0.0 must not be treated as loopback", server.loopbackBind)
  }

  @Test
  void rejectsMissingAndWrongTokensAndAcceptsTheRealOne() {
    server = newServer()
    server.authToken = 'test-token-value'
    int port = freePort()
    server.port = port
    server.start()

    String url = "http://127.0.0.1:${port}/mcp"
    String body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

    assertEquals("no token must be rejected", 401, post(url, null, body).status)
    assertEquals("wrong token must be rejected", 401, post(url, 'wrong', body).status)
    assertEquals("missing bearer prefix must be rejected", 401,
        post(url, null, body, 'test-token-value').status)

    def ok = post(url, 'test-token-value', body)
    assertEquals("correct token must be accepted", 200, ok.status)
    assertTrue("handler should have run", ok.body.contains('handled'))
  }

  @Test
  void unauthorizedResponseIsJsonRpcShaped() {
    server = newServer()
    server.authToken = 'test-token-value'
    int port = freePort()
    server.port = port
    server.start()

    def denied = post("http://127.0.0.1:${port}/mcp", null,
        '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')

    assertEquals(401, denied.status)
    assertTrue("should carry a JSON-RPC error code", denied.body.contains('-32001'))
    assertFalse("must not leak the expected token", denied.body.contains('test-token-value'))
  }

  private McpHttpServer newServer() {
    def httpServer = new McpHttpServer()
    httpServer.transport = new McpSseTransport()
    httpServer.mcpServer = new McpServer() {
      @Override
      McpResponse handleRequest(McpRequest request) {
        new McpResponse(id: request?.id, result: [status: 'handled'])
      }
    }
    return httpServer
  }

  /** Posts a body, optionally with a bearer token or a raw Authorization value. */
  private static Map post(String url, String bearer, String body, String rawAuth = null) {
    HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection()
    conn.requestMethod = 'POST'
    conn.doOutput = true
    conn.connectTimeout = 5000
    conn.readTimeout = 5000
    if (bearer) {
      conn.setRequestProperty('Authorization', "Bearer ${bearer}")
    } else if (rawAuth) {
      conn.setRequestProperty('Authorization', rawAuth)
    }
    conn.outputStream.withWriter('UTF-8') { it.write(body) }
    int status = conn.responseCode
    String text = ''
    try {
      def stream = status >= 400 ? conn.errorStream : conn.inputStream
      if (stream != null) {
        text = stream.getText('UTF-8')
      }
    } catch (IOException ignored) {
    } finally {
      conn.disconnect()
    }
    return [status: status, body: text]
  }

  private static int freePort() {
    def socket = new ServerSocket(0)
    try {
      return socket.localPort
    } finally {
      socket.close()
    }
  }
}
