package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.config.JdbsAlias
import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import com.archon.jdbsee.jdbcli.mcp.cache.JdbiCache
import org.jdbi.v3.core.Jdbi
import org.junit.After
import org.junit.Before
import org.junit.Test

import static org.junit.Assert.*

/**
 * Schema tools must return structured errors instead of throwing, and
 * list_aliases must never surface a password from a JDBC URL.
 */
class SchemaAndAliasToolTest {

  private static final String URL = 'jdbc:hsqldb:mem:schematooltest'

  private Jdbi jdbi
  private SchemaTool tool

  @Before
  void setUp() {
    jdbi = Jdbi.create(URL, 'sa', '')
    jdbi.useHandle { handle ->
      handle.execute('CREATE TABLE people (id INT PRIMARY KEY, name VARCHAR(40))')
    }
    tool = new SchemaTool(jdbiCache: fixedCache(jdbi))
    tool.defaultAlias = 'local'
  }

  @After
  void tearDown() {
    jdbi?.useHandle { handle -> handle.execute('DROP TABLE people') }
  }

  @Test
  void describeTableReturnsColumnDetail() {
    def result = tool.execute([action: 'describe_table', table_name: 'PEOPLE'])

    assertTrue(result.success)
    assertEquals('PEOPLE', result.table_name)
    assertEquals(['ID', 'NAME'], result.columns*.column_name)
    assertFalse("a primary key column is not nullable",
        result.columns.find { it.column_name == 'ID' }.nullable)
    assertEquals(40, result.columns.find { it.column_name == 'NAME' }.column_size)
  }

  @Test
  void listTablesFindsTheCreatedTable() {
    def result = tool.execute([action: 'list_tables', pattern: 'PEOPLE'])

    assertTrue(result.success)
    assertEquals(['PEOPLE'], result.tables*.table_name)
  }

  @Test
  void schemaFailuresBecomeStructuredErrors() {
    def unknownAlias = tool.execute([action: 'list_tables', alias: 'nope'])
    assertFalse(unknownAlias.success)
    assertEquals('INVALID_ARGUMENT', unknownAlias.error.code)

    def describeUnknownAlias = tool.execute([action: 'describe_table', table_name: 'PEOPLE', alias: 'nope'])
    assertFalse(describeUnknownAlias.success)
    assertEquals('INVALID_ARGUMENT', describeUnknownAlias.error.code)

    def noTableName = tool.execute([action: 'describe_table'])
    assertFalse(noTableName.success)
    assertEquals('INVALID_ARGUMENT', noTableName.error.code)

    def badAction = tool.execute([action: 'nonsense'])
    assertFalse(badAction.success)
    assertEquals('INVALID_ACTION', badAction.error.code)
  }

  @Test
  void describeTableOnAMissingTableReportsNoColumnsRatherThanThrowing() {
    def result = tool.execute([action: 'describe_table', table_name: 'NO_SUCH_TABLE'])

    assertTrue("JDBC metadata lookup succeeds with an empty column list", result.success)
    assertTrue(result.columns.isEmpty())
  }

  @Test
  void listAliasesMasksCredentialsInEveryUrlForm() {
    def tool = new ListAliasesTool(aliasDao: aliasDaoWith([
        alias('userinfo', 'jdbc:postgresql://appuser:s3cret@db:5432/app'),
        alias('queryParam', 'jdbc:postgresql://db:5432/app?user=appuser&password=s3cret'),
        alias('pwdParam', 'jdbc:mysql://db:3306/app?pwd=s3cret&useSSL=true'),
        alias('noCredentials', 'jdbc:hsqldb:mem:app')
    ]))
    tool.defaultAlias = 'userinfo'

    def result = tool.execute([:])
    def urls = result.aliases.collectEntries { [it.name, it.url] }

    assertTrue(result.success)
    urls.each { name, url ->
      assertFalse("password leaked for ${name}: ${url}", url.contains('s3cret'))
    }
    assertEquals('jdbc:postgresql://***:***@db:5432/app', urls.userinfo)
    assertEquals('jdbc:postgresql://db:5432/app?user=appuser&password=***', urls.queryParam)
    assertEquals('jdbc:mysql://db:3306/app?pwd=***&useSSL=true', urls.pwdParam)
    assertEquals("a URL without credentials must be left alone",
        'jdbc:hsqldb:mem:app', urls.noCredentials)
  }

  private static JdbsAlias alias(String name, String url) {
    new JdbsAlias(name: name, url: url)
  }

  private static JdbsAliasDao aliasDaoWith(List<JdbsAlias> aliases) {
    new JdbsAliasDao() {
      @Override
      List<JdbsAlias> listAliases() {
        return aliases
      }
    }
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
}
