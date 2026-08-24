package com.archon.jdbsee.jdbcli

import groovy.json.JsonSlurper
import org.junit.BeforeClass
import org.junit.Test

import static org.junit.Assert.*

/**
 * Process-level contract for `query`: a known failure exits non-zero in every
 * format, --pretty changes only whitespace, and the deprecated json-pretty
 * keeps its current behaviour until it is removed.
 *
 * These run the real CLI because the contract being pinned is the exit status,
 * which only exists at process level.
 */
class QueryExitContractTest {

  private static final String DB = 'jdbc:hsqldb:mem:exitcontract'
  private static String classpath
  private static File home

  @BeforeClass
  static void prepare() {
    classpath = System.getProperty('java.class.path')
    home = File.createTempDir('exit-contract', '')
    home.deleteOnExit()
  }

  private static Map run(List<String> args) {
    def cmd = ['java', "-Duser.home=${home.absolutePath}", '-cp', classpath,
               'com.archon.jdbsee.jdbcli.CliApplication'] + args
    def proc = cmd.execute()
    def out = new StringBuilder()
    def err = new StringBuilder()
    proc.waitForProcessOutput(out, err)
    return [exit: proc.exitValue(), out: out.toString(), err: err.toString()]
  }

  private static List<String> queryArgs(String sql, List<String> extra = []) {
    ['query', '-c', 'org.hsqldb.jdbc.JDBCDriver', '-l', DB, '-u', 'sa'] + extra + [sql]
  }

  /** Every format must signal a rejected statement through the exit status. */
  @Test
  void aRejectedStatementExitsNonZeroInEveryFormat() {
    ['json', 'markdown', 'csv', 'json-pretty'].each { format ->
      def result = run(queryArgs('UPDATE t SET id = 1', ['-f', format]))

      assertEquals("${format} must exit non-zero on a rejected statement",
          1, result.exit)
      assertFalse("${format} must still report the failure on stdout",
          result.out.trim().isEmpty())
    }
  }

  @Test
  void aSuccessfulQueryExitsZeroInEveryFormat() {
    ['json', 'markdown', 'csv', 'json-pretty'].each { format ->
      def result = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', format]))

      assertEquals("${format} must exit zero on success", 0, result.exit)
    }
  }

  @Test
  void aConnectionFailureExitsNonZeroWithAStructuredError() {
    def cmd = ['query', '-c', 'org.hsqldb.jdbc.JDBCDriver',
               '-l', 'jdbc:hsqldb:hsql://127.0.0.1:1/nope', '-u', 'sa',
               '-f', 'json', 'SELECT 1 FROM (VALUES(0))']
    def result = run(cmd)

    assertEquals(1, result.exit)
    assertEquals('DB_300', new JsonSlurper().parseText(result.out.trim()).error.code)
  }

  /** --pretty must be a whitespace-only change, so both parse to one object. */
  @Test
  void prettyJsonParsesToTheSameStructureAsCompactJson() {
    def compact = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', 'json']))
    def pretty = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', 'json', '--pretty']))

    assertEquals(0, compact.exit)
    assertEquals(0, pretty.exit)

    def slurper = new JsonSlurper()
    assertEquals(slurper.parseText(compact.out.trim()), slurper.parseText(pretty.out.trim()))
    assertTrue("pretty output should span multiple lines",
        pretty.out.trim().readLines().size() > 1)
    assertEquals("compact output should be a single line",
        1, compact.out.trim().readLines().size())
  }

  @Test
  void prettyAppliesToErrorEnvelopesToo() {
    def result = run(queryArgs('UPDATE t SET id = 1', ['-f', 'json', '--pretty']))

    assertEquals(1, result.exit)
    assertTrue(result.out.trim().readLines().size() > 1)
    assertEquals('SQL_100', new JsonSlurper().parseText(result.out.trim()).error.code)
  }

  @Test
  void prettyDoesNotAlterNonJsonFormats() {
    ['csv', 'markdown'].each { format ->
      def plain = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', format]))
      def pretty = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', format, '--pretty']))

      assertEquals("--pretty must not change ${format}", plain.out, pretty.out)
    }
  }

  @Test
  void deprecatedFormatWarnsOnStderrWithoutChangingStdout() {
    def result = run(queryArgs('SELECT 1 AS n FROM (VALUES(0))', ['-f', 'json-pretty']))

    assertTrue("should warn about the deprecation",
        result.err.contains('json-pretty is deprecated'))
    assertTrue("should point at the replacement", result.err.contains('--pretty'))
    assertTrue("the legacy bare-array output is unchanged", result.out.trim().startsWith('['))
  }

  /**
   * Migration-period compatibility, pinned deliberately: json-pretty writes a
   * validation error to stdout and never creates the -o file. Fixing this is
   * out of scope while the format is deprecated; the assertion exists so the
   * behaviour cannot drift unnoticed before removal.
   */
  @Test
  void deprecatedFormatStillBypassesTheOutputFileOnRejection() {
    File target = new File(home, 'legacy-out.json')
    target.delete()

    def result = run(queryArgs('UPDATE t SET id = 1',
        ['-f', 'json-pretty', '-o', target.absolutePath]))

    assertEquals(1, result.exit)
    assertFalse("legacy rejection does not create the -o file", target.exists())
    assertTrue("it goes to stdout instead", result.out.contains('SQL_100'))
  }

  /**
   * Migration-period behaviour: the legacy path keeps running the remaining
   * statements after a rejection, so an existing script executes exactly what
   * it did before. Only the exit status changed.
   */
  @Test
  void legacyMultiStatementStillRunsTheRestAndThenExitsNonZero() {
    def result = run(['query', '-c', 'org.hsqldb.jdbc.JDBCDriver', '-l', DB, '-u', 'sa',
                      '-f', 'json-pretty',
                      '-x', 'UPDATE t SET id = 1',
                      'SELECT 1 AS n FROM (VALUES(0))'])

    assertEquals("the command must fail overall", 1, result.exit)
    assertTrue("the rejection is reported", result.out.contains('SQL_100'))
    assertTrue("the valid query after it still produced output",
        result.out.contains('"n"') || result.out.contains('N'))
  }

  @Test
  void structuredFormatsHonourTheOutputFileOnRejection() {
    File target = new File(home, 'structured-out.json')
    target.delete()

    def result = run(queryArgs('UPDATE t SET id = 1',
        ['-f', 'json', '-o', target.absolutePath]))

    assertEquals(1, result.exit)
    assertTrue("the error envelope belongs in the -o file", target.exists())
    assertEquals('SQL_100',
        new JsonSlurper().parseText(target.text).error.code)
    assertTrue("stdout stays empty when -o is given", result.out.trim().isEmpty())
  }
}
