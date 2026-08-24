package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.QueryService
import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import com.archon.jdbsee.jdbcli.mcp.cache.QueryResultCache
import com.archon.jdbsee.jdbcli.security.SqlSecurityValidator
import org.jdbi.v3.core.Jdbi
import org.junit.After
import org.junit.Before
import org.junit.Test

import javax.sql.DataSource
import java.lang.reflect.InvocationHandler
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Method
import java.lang.reflect.Proxy
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.util.logging.Logger

import static org.junit.Assert.*

/**
 * execute_query must cap rows while streaming (an oversized result set must not
 * be materialised first) and must report failures as structured errors rather
 * than letting them escape to the JSON-RPC layer.
 */
class QueryToolLimitsTest {

  private static final String URL = 'jdbc:hsqldb:mem:querytoollimits'

  private Jdbi jdbi
  private QueryTool tool

  @Before
  void setUp() {
    jdbi = Jdbi.create(URL, 'sa', '')
    jdbi.useHandle { handle ->
      handle.execute('CREATE TABLE items (id INT PRIMARY KEY, label VARCHAR(20))')
      (1..7).each { handle.execute("INSERT INTO items VALUES (${it}, 'row-${it}')" as String) }
    }

    tool = new QueryTool(
        queryService: new QueryService(new SqlSecurityValidator(), new FormatterFactory()),
        cache: new QueryResultCache(),
        jdbiCache: fixedCache(jdbi)
    )
    tool.defaultAlias = 'local'
  }

  @After
  void tearDown() {
    jdbi?.useHandle { handle -> handle.execute('DROP TABLE items') }
  }

  @Test
  void capsRowsAndFlagsTruncationOnlyWhenRowsWereDropped() {
    tool.maxResults = 3

    def over = tool.execute([sql: 'SELECT id FROM items ORDER BY id'])
    assertTrue(over.success)
    assertEquals("must stop at the cap", 3, over.metadata.row_count)
    assertTrue("7 rows past a cap of 3 is a truncation", over.metadata.truncated)
    assertEquals(3, over.metadata.max_results)

    def exact = tool.execute([sql: 'SELECT id FROM items WHERE id <= 3 ORDER BY id'])
    assertEquals(3, exact.metadata.row_count)
    assertFalse("a result exactly at the cap is complete", exact.metadata.truncated)

    def under = tool.execute([sql: 'SELECT id FROM items WHERE id <= 2 ORDER BY id'])
    assertEquals(2, under.metadata.row_count)
    assertFalse(under.metadata.truncated)
  }

  @Test
  void cachesOnlyTheCappedRowsSoGetResultCannotExceedTheLimit() {
    tool.maxResults = 2
    def cache = new QueryResultCache()
    tool.cache = cache

    def result = tool.execute([sql: 'SELECT id FROM items ORDER BY id'])

    def cached = cache.get(result.result_id as String)
    assertTrue(cached.present)
    assertEquals("cache must not hold rows beyond the cap", 2, cached.get().data.size())
  }

  /**
   * Column labels and types must come from the same ResultSet as the rows.
   * The connection is wrapped so that every prepareStatement/createStatement
   * carrying the query text is counted: running the SQL twice would show up as 2.
   */
  @Test
  void reportsColumnMetadataWithoutRunningTheQueryTwice() {
    String sql = 'SELECT id, label FROM items ORDER BY id'
    def counter = new StatementCounter(sql)
    def countingJdbi = Jdbi.create(counter.wrap(URL, 'sa', ''))
    tool.jdbiCache = fixedCache(countingJdbi)
    tool.maxResults = 10

    def result = tool.execute([sql: sql])

    assertTrue(result.success)
    assertEquals(['ID', 'LABEL'], result.metadata.columns)
    assertEquals(2, result.metadata.column_count)
    assertEquals("the SQL must be prepared exactly once", 1, counter.count)

    // Column types are carried on the cached result rather than the tool response.
    def cached = tool.cache.get(result.result_id as String)
    assertTrue(cached.present)
    assertEquals(['java.lang.Integer', 'java.lang.String'], cached.get().metadata.columnTypes)
  }

  @Test
  void turnsFailuresIntoStructuredErrors() {
    tool.maxResults = 10

    def missingTable = tool.execute([sql: 'SELECT id FROM no_such_table'])
    assertFalse(missingTable.success)
    assertEquals('QUERY_EXECUTION_ERROR', missingTable.error.code)

    def unknownAlias = tool.execute([sql: 'SELECT id FROM items', alias: 'nope'])
    assertFalse(unknownAlias.success)
    assertEquals('INVALID_ARGUMENT', unknownAlias.error.code)

    def write = tool.execute([sql: 'DELETE FROM items'])
    assertFalse(write.success)
    assertEquals('SECURITY_VALIDATION_FAILED', write.error.code)

    def empty = tool.execute([:])
    assertFalse(empty.success)
    assertEquals('INVALID_ARGUMENT', empty.error.code)
  }

  private static JdbiCache fixedCache(Jdbi jdbi) {
    new JdbiCache(null, null) {
      @Override
      Jdbi getJdbi(String alias) {
        if (alias != 'local') {
          throw new IllegalArgumentException("Alias not found: ${alias}")
        }
        return jdbi
      }
    }
  }

  /**
   * Counts how many times a statement was created for a specific SQL text,
   * via a DataSource that proxies Connection.prepareStatement/createStatement.
   */
  private static class StatementCounter {
    private final String watched
    int count = 0

    StatementCounter(String watched) {
      this.watched = watched
    }

    DataSource wrap(String url, String user, String password) {
      def counter = this
      return new DataSource() {
        @Override
        Connection getConnection() {
          return proxy(DriverManager.getConnection(url, user, password), counter)
        }

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

    private static Connection proxy(Connection real, StatementCounter counter) {
      return (Connection) Proxy.newProxyInstance(
          Connection.classLoader, [Connection] as Class[], new InvocationHandler() {
        @Override
        Object invoke(Object p, Method method, Object[] args) {
          if ((method.name == 'prepareStatement' || method.name == 'createStatement')
              && args?.length > 0 && counter.watched == args[0]) {
            counter.count++
          }
          try {
            return method.invoke(real, args)
          } catch (InvocationTargetException e) {
            throw e.targetException
          }
        }
      })
    }
  }
}
