package com.archon.jdbsee.jdbcli.mcp.server

import com.archon.jdbsee.jdbcli.model.McpError
import com.archon.jdbsee.jdbcli.model.McpResponse
import org.eclipse.jetty.server.Server
import org.eclipse.jetty.servlet.ServletContextHandler
import org.eclipse.jetty.servlet.ServletHolder
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton
import javax.servlet.http.HttpServlet
import javax.servlet.http.HttpServletRequest
import javax.servlet.http.HttpServletResponse
import java.net.InetSocketAddress
import java.security.SecureRandom
import java.util.Base64

/**
 * Jetty HTTP wrapper exposing the MCP server over Server-Sent Events.
 *
 * GET  /mcp - opens the SSE stream (greets with a "connected" event,
 *             then emits a heartbeat every 30s to keep the connection alive).
 * POST /mcp - accepts a JSON-RPC request body, returns the SSE-encoded
 *             response (client posts to the SSE stream).
 *
 * The endpoint exposes decrypted database credentials, so it binds to the
 * loopback interface by default and requires a bearer token on every request.
 * Both can be relaxed explicitly, but a non-loopback bind is logged as a
 * warning because it makes the database reachable from the network.
 */
@Singleton
class McpHttpServer {
  private static final Logger LOG = LoggerFactory.getLogger(McpHttpServer)
  private static final String LOOPBACK = '127.0.0.1'

  McpServer mcpServer

  McpSseTransport transport

  private Server jettyServer

  int port = 8080

  String bindHost = LOOPBACK

  /** Bearer token required on every request; generated when not supplied. */
  String authToken

  @Inject
  McpHttpServer() {}

  void setMcpServer(McpServer mcpServer) {
    this.mcpServer = mcpServer
  }

  void setTransport(McpSseTransport transport) {
    this.transport = transport
  }

  void setPort(int port) {
    this.port = port
  }

  void setBindHost(String bindHost) {
    this.bindHost = bindHost ?: LOOPBACK
  }

  void setAuthToken(String authToken) {
    this.authToken = authToken
  }

  /** Generates a token when none was configured. Returns the effective token. */
  String ensureAuthToken() {
    if (!authToken) {
      byte[] buf = new byte[32]
      SecureRandom.getInstanceStrong().nextBytes(buf)
      authToken = Base64.urlEncoder.withoutPadding().encodeToString(buf)
    }
    return authToken
  }

  boolean isLoopbackBind() {
    bindHost == LOOPBACK || bindHost == '::1' || bindHost == 'localhost'
  }

  void start() throws Exception {
    ensureAuthToken()
    jettyServer = new Server(new InetSocketAddress(bindHost, port))
    def context = new ServletContextHandler()
    context.contextPath = "/"
    jettyServer.handler = context
    context.addServlet(new ServletHolder(new SseServlet()), "/mcp")
    jettyServer.start()
    if (!loopbackBind) {
      LOG.warn('MCP 服务器绑定在 {} 上，数据库将可从网络访问；请确认这是有意的', bindHost)
    }
    LOG.info("MCP HTTP Server started on {}:{}", bindHost, port)
  }

  void stop() throws Exception {
    if (jettyServer) {
      jettyServer.stop()
      LOG.info("MCP HTTP Server stopped")
    }
  }

  /**
   * Serves the SSE endpoint: the GET stream and the POST request sink.
   */
  class SseServlet extends HttpServlet {

    /**
     * Rejects a request that does not carry the expected bearer token.
     * Returns true when the request was rejected.
     */
    private boolean rejectUnauthorized(HttpServletRequest req, HttpServletResponse resp) {
      if (isAuthorized(req)) {
        return false
      }
      LOG.warn('拒绝未授权的 MCP 请求，来自 {}', req.remoteAddr)
      resp.status = 401
      resp.setContentType('application/json')
      resp.setCharacterEncoding('UTF-8')
      resp.setHeader('WWW-Authenticate', 'Bearer realm="jdbsee-mcp"')
      resp.writer.write('{"jsonrpc":"2.0","id":null,"error":{"code":-32001,' +
          '"message":"Unauthorized: missing or invalid bearer token"}}')
      resp.writer.flush()
      return true
    }

    private boolean isAuthorized(HttpServletRequest req) {
      String expected = authToken
      if (!expected) {
        return false
      }
      String header = req.getHeader('Authorization')
      String presented = null
      if (header?.regionMatches(true, 0, 'Bearer ', 0, 7)) {
        presented = header.substring(7).trim()
      }
      if (!presented) {
        return false
      }
      // Constant-time comparison to avoid leaking the token byte by byte.
      byte[] a = presented.getBytes('UTF-8')
      byte[] b = expected.getBytes('UTF-8')
      return java.security.MessageDigest.isEqual(a, b)
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) {
      if (rejectUnauthorized(req, resp)) {
        return
      }
      resp.setContentType("text/event-stream")
      resp.setCharacterEncoding("UTF-8")
      resp.setHeader("Cache-Control", "no-cache")
      resp.setHeader("Connection", "keep-alive")
      try {
        def writer = resp.writer
        def initResponse = new McpResponse(id: null, result: [status: "connected", server: "jdbsee-mcp"])
        writer.write(transport.toSseEvent(initResponse))
        writer.flush()
        while (!resp.isCommitted()) {
          Thread.sleep(30000)
          writer.write(transport.createHeartbeat())
          writer.flush()
        }
      } catch (Exception e) {
        LOG.error("SSE connection error", e)
      }
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) {
      if (rejectUnauthorized(req, resp)) {
        return
      }
      resp.setContentType("application/json")
      resp.setCharacterEncoding("UTF-8")
      try {
        def requestBody = req.reader.text
        def mcpRequest = transport.parseRequest(requestBody)
        def mcpResponse = mcpServer.handleRequest(mcpRequest)
        def writer = resp.writer
        writer.write(transport.toSseEvent(mcpResponse))
        writer.flush()
      } catch (Exception e) {
        LOG.error("Error processing POST request", e)
        resp.status = 500
        def errorResponse = new McpResponse(
            id: null,
            error: new McpError(code: -32603, message: "Internal error", data: [error: e.message])
        )
        resp.writer.write(transport.toSseEvent(errorResponse))
      }
    }
  }
}
