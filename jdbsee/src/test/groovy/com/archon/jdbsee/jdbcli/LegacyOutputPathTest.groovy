package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.exception.CommandFailedException
import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.format.JsonAiResponseFormatter
import com.archon.jdbsee.jdbcli.security.SqlSecurityValidator
import groovy.json.JsonSlurper
import org.jdbi.v3.core.Jdbi
import org.junit.After
import org.junit.Before
import org.junit.Test

import static org.junit.Assert.*

/**
 * `-f json-pretty` uses the legacy ConsoleService renderer rather than the AI
 * formatters, so its output shape differs from the other formats. These tests
 * pin that difference, including the inconsistency in how it reports a
 * validation rejection.
 */
class LegacyOutputPathTest {

  private static final String URL = 'jdbc:hsqldb:mem:legacyoutput'

  private Jdbi jdbi
  private QueryService service
  private ByteArrayOutputStream captured
  private PrintStream originalOut

  @Before
  void setUp() {
    jdbi = Jdbi.create(URL, 'sa', '')
    jdbi.useHandle { handle ->
      handle.execute('CREATE TABLE t (id INT, n VARCHAR(10))')
      handle.execute("INSERT INTO t VALUES (1, 'a')")
    }
    service = new QueryService(new SqlSecurityValidator(),
        new FormatterFactory(new JsonAiResponseFormatter(), null, null))

    captured = new ByteArrayOutputStream()
    originalOut = System.out
    System.out = new PrintStream(captured, true, 'UTF-8')
  }

  @After
  void tearDown() {
    System.out = originalOut
    jdbi?.useHandle { handle -> handle.execute('DROP TABLE t') }
  }

  @Test
  void legacyFormatIsResolvedFromItsHyphenatedCode() {
    // The code is json-pretty, not json_pretty: an underscore falls back to
    // JSON and silently takes the structured path instead.
    assertEquals(OutputFormat.JSON_PRETTY, OutputFormat.fromCode('json-pretty'))
    assertEquals(OutputFormat.JSON, OutputFormat.fromCode('json_pretty'))
  }

  @Test
  void legacyPathRunsTheCallbackForEachStatement() {
    int calls = 0
    QueryCallback<Void, Exception> callback = { query ->
      calls++
      return null
    }

    service.execute(jdbi, callback, 'SELECT id FROM t', 'SELECT n FROM t')

    assertEquals("one callback invocation per statement", 2, calls)
  }

  /**
   * A rejected statement never reaches the database, and the command now fails
   * so the caller sees a non-zero exit rather than a rejection buried in the
   * output. The rejection itself is still printed as an AI-style JSON envelope
   * even for json-pretty, whose success output is a bare array — the two shapes
   * do not match, which is why the format is deprecated.
   */
  @Test
  void aRejectedStatementSkipsTheCallbackAndFailsTheCommand() {
    boolean called = false
    QueryCallback<Void, Exception> callback = { query ->
      called = true
      return null
    }

    try {
      service.execute(jdbi, callback, 'UPDATE t SET id = 2')
      fail('a rejected statement must fail the command')
    } catch (CommandFailedException expected) {
      assertTrue(expected.message.contains('SQL_100'))
    }

    assertFalse("a rejected statement must not reach the database", called)
    def parsed = new JsonSlurper().parseText(captured.toString('UTF-8').trim())
    assertFalse(parsed.success)
    assertEquals('SQL_100', parsed.error.code)
  }

  /**
   * The legacy execution strategy is preserved: a rejection does not stop the
   * run, so a script keeps executing the same statements it did before. Only
   * the final exit status changes.
   */
  @Test
  void aRejectionDoesNotStopTheRunButStillFailsTheCommand() {
    def seen = []
    QueryCallback<Void, Exception> callback = { query ->
      seen << 'ran'
      return null
    }

    try {
      service.execute(jdbi, callback, 'UPDATE t SET id = 2', 'SELECT id FROM t')
      fail('the command must report failure once everything has run')
    } catch (CommandFailedException expected) {
      assertTrue(expected.message.contains('SQL_100'))
    }

    assertEquals("the valid statement after the rejection must still run", 1, seen.size())
  }

  @Test
  void reportsTheFirstRejectionWhenSeveralStatementsAreRejected() {
    def seen = []
    QueryCallback<Void, Exception> callback = { query ->
      seen << 'ran'
      return null
    }

    try {
      service.execute(jdbi, callback,
          'UPDATE t SET id = 2', 'SELECT id FROM t', 'DELETE FROM t')
      fail('the command must report failure')
    } catch (CommandFailedException expected) {
      assertTrue("the first rejection is the one reported",
          expected.message.contains('SQL_100'))
    }

    assertEquals("the valid statement still runs", 1, seen.size())
    // Both rejections are reported on stdout, preserving the multi-part output.
    assertEquals("each rejection is printed", 2,
        captured.toString('UTF-8').count('"success":false'))
  }

  @Test
  void aNullDataSourceIsRejectedBeforeUse() {
    try {
      service.execute((javax.sql.DataSource) null, { q -> null } as QueryCallback)
      fail('should have rejected a null DataSource')
    } catch (IllegalArgumentException e) {
      assertTrue(e.message.contains('DataSource cannot be null'))
    }
  }

  @Test
  void getFormatterFallsBackWhenTheFactoryIsMissing() {
    def bare = new QueryService(new SqlSecurityValidator(), null)

    assertEquals('JsonAiResponseFormatter', bare.getFormatter('json').class.simpleName)
    assertEquals('MarkdownAiResponseFormatter', bare.getFormatter('markdown').class.simpleName)
    assertEquals('CsvAiResponseFormatter', bare.getFormatter('csv').class.simpleName)
    assertEquals("an unknown code falls back to JSON",
        'JsonAiResponseFormatter', bare.getFormatter('nonsense').class.simpleName)
  }
}
