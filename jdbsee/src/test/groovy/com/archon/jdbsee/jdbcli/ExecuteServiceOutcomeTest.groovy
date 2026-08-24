package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.security.DmlSecurityValidator
import groovy.json.JsonSlurper
import org.junit.After
import org.junit.Before
import org.junit.Test

import javax.sql.DataSource
import java.io.PrintWriter
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.util.logging.Logger

import static org.junit.Assert.*

/**
 * executeBatchWithOutcome must report whether the run failed so the CLI can set
 * a non-zero exit status; previously a failed execute still exited 0.
 */
class ExecuteServiceOutcomeTest {

  private static final String URL = 'jdbc:hsqldb:mem:executeoutcome'

  private ExecuteService service
  private DataSource dataSource

  @Before
  void setUp() {
    service = new ExecuteService(new DmlSecurityValidator(), new FormatterFactory())
    dataSource = dataSourceFor(URL)
    withConnection { conn ->
      conn.createStatement().execute('CREATE TABLE t (id INT PRIMARY KEY)')
    }
  }

  @After
  void tearDown() {
    withConnection { conn -> conn.createStatement().execute('DROP TABLE t') }
  }

  @Test
  void aSuccessfulSingleStatementIsNotReportedAsFailed() {
    def outcome = service.executeBatchWithOutcome(dataSource, 'INSERT INTO t VALUES (1)', 'json')

    assertFalse(outcome.failed)
    assertTrue(new JsonSlurper().parseText(outcome.output).success)
  }

  @Test
  void aFailedSingleStatementIsReportedAsFailed() {
    def outcome = service.executeBatchWithOutcome(dataSource, 'INSERT INTO nope VALUES (1)', 'json')

    assertTrue("a SQL error must be reported as failed", outcome.failed)
    def parsed = new JsonSlurper().parseText(outcome.output)
    assertFalse(parsed.success)
    assertEquals('DB_301', parsed.error.code)
  }

  @Test
  void aPartiallyFailedBatchIsReportedAsFailed() {
    def outcome = service.executeBatchWithOutcome(dataSource,
        'INSERT INTO t VALUES (10); INSERT INTO nope VALUES (11); INSERT INTO t VALUES (12)', 'json')

    assertTrue(outcome.failed)
    def parsed = new JsonSlurper().parseText(outcome.output)
    assertEquals(3, parsed.summary.total)
    assertEquals(1, parsed.summary.failure_count)

    // Execution continues after a failure and nothing is rolled back.
    assertEquals([10, 12], selectIds().findAll { it >= 10 })
  }

  @Test
  void aFullySuccessfulBatchIsNotReportedAsFailed() {
    def outcome = service.executeBatchWithOutcome(dataSource,
        'INSERT INTO t VALUES (20); INSERT INTO t VALUES (21)', 'json')

    assertFalse(outcome.failed)
    assertEquals(0, new JsonSlurper().parseText(outcome.output).summary.failure_count)
  }

  @Test
  void aConnectionFailureIsClassifiedAsDb300() {
    def unreachable = dataSourceFor('jdbc:hsqldb:hsql://127.0.0.1:1/nope')

    def outcome = service.executeBatchWithOutcome(unreachable, 'INSERT INTO t VALUES (1)', 'json')

    assertTrue(outcome.failed)
    assertEquals('DB_300', new JsonSlurper().parseText(outcome.output).error.code)
  }

  @Test
  void emptyInputIsReportedAsFailed() {
    def outcome = service.executeBatchWithOutcome(dataSource, '   ', 'json')

    assertTrue(outcome.failed)
    assertFalse(new JsonSlurper().parseText(outcome.output).success)
  }

  private List<Integer> selectIds() {
    def ids = []
    withConnection { conn ->
      def rs = conn.createStatement().executeQuery('SELECT id FROM t ORDER BY id')
      while (rs.next()) {
        ids << rs.getInt(1)
      }
      rs.close()
    }
    return ids
  }

  private void withConnection(Closure body) {
    Connection conn = dataSource.connection
    try {
      body(conn)
    } finally {
      conn.close()
    }
  }

  private static DataSource dataSourceFor(String url) {
    return new DataSource() {
      @Override
      Connection getConnection() { return DriverManager.getConnection(url, 'sa', '') }

      @Override
      Connection getConnection(String u, String p) { return getConnection() }

      @Override
      PrintWriter getLogWriter() { return null }

      @Override
      void setLogWriter(PrintWriter out) {}

      @Override
      void setLoginTimeout(int seconds) {}

      @Override
      int getLoginTimeout() { return 0 }

      @Override
      Logger getParentLogger() { return null }

      @Override
      def <T> T unwrap(Class<T> iface) { throw new SQLException('unsupported') }

      @Override
      boolean isWrapperFor(Class<?> iface) { return false }
    }
  }
}
