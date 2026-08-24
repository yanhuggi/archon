package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.model.QueryResult
import com.archon.jdbsee.jdbcli.security.ValidationResult
import groovy.json.JsonSlurper
import org.junit.Test

import static org.junit.Assert.*

/**
 * The formatters carry the output contract every agent workflow parses, so the
 * envelope shapes and the escaping rules are pinned here.
 */
class FormatterTest {

  private final JsonAiResponseFormatter json = new JsonAiResponseFormatter()
  private final CsvAiResponseFormatter csv = new CsvAiResponseFormatter()
  private final MarkdownAiResponseFormatter markdown = new MarkdownAiResponseFormatter()

  private static QueryResult sample() {
    new QueryResult(
        columns: ['ID', 'NAME'],
        columnTypes: ['java.lang.Integer', 'java.lang.String'],
        rows: [[1, 'Alice'], [2, 'Bob']],
        rowCount: 2,
        columnCount: 2
    )
  }

  @Test
  void jsonSuccessMatchesTheDocumentedShape() {
    def parsed = new JsonSlurper().parseText(
        json.formatSuccess(sample(), OutputFormat.JSON, MetadataLevel.BASIC))

    assertTrue(parsed.success)
    assertEquals(['ID', 'NAME'], parsed.data.columns)
    assertEquals(['java.lang.Integer', 'java.lang.String'], parsed.data.types)
    assertEquals([[1, 'Alice'], [2, 'Bob']], parsed.data.rows)
    assertEquals([[ID: 1, NAME: 'Alice'], [ID: 2, NAME: 'Bob']], parsed.data.objects)
    assertEquals(2, parsed.data.rowCount)
    assertEquals(2, parsed.metadata.rowCount)
    assertEquals(2, parsed.metadata.columnCount)
  }

  @Test
  void jsonErrorMatchesTheDocumentedShape() {
    def parsed = new JsonSlurper().parseText(json.formatError(
        ValidationResult.invalid('SQL_100', 'Only SELECT statements are allowed',
            'Detected statement type: Update'),
        'UPDATE users SET active = true'))

    assertFalse(parsed.success)
    assertEquals('SQL_100', parsed.error.code)
    assertEquals('Only SELECT statements are allowed', parsed.error.message)
    assertEquals('Detected statement type: Update', parsed.error.details)
    assertEquals('UPDATE users SET active = true', parsed.metadata.query)
    assertTrue("timestamp should be a real epoch value", parsed.metadata.timestamp > 0)
  }

  @Test
  void jsonKeepsNullsAndStringifiesUnknownTypes() {
    def result = new QueryResult(
        columns: ['A', 'B'],
        columnTypes: ['java.lang.String', 'java.lang.Object'],
        rows: [[null, new Object() {
          @Override
          String toString() { return 'opaque' }
        }]],
        rowCount: 1, columnCount: 2
    )

    def parsed = new JsonSlurper().parseText(
        json.formatSuccess(result, OutputFormat.JSON, MetadataLevel.BASIC))

    assertNull(parsed.data.rows[0][0])
    assertEquals('opaque', parsed.data.rows[0][1])
  }

  @Test
  void jsonEscapesQuotesAndNewlinesSoOutputStaysParseable() {
    def result = new QueryResult(
        columns: ['TEXT'], columnTypes: ['java.lang.String'],
        rows: [['say "hi"'], ['line1\nline2']], rowCount: 2, columnCount: 1
    )

    // Would throw if the payload were not valid JSON.
    def parsed = new JsonSlurper().parseText(
        json.formatSuccess(result, OutputFormat.JSON, MetadataLevel.BASIC))

    assertEquals('say "hi"', parsed.data.rows[0][0])
    assertEquals('line1\nline2', parsed.data.rows[1][0])
  }

  @Test
  void csvQuotesValuesContainingSeparatorsQuotesOrNewlines() {
    def result = new QueryResult(
        columns: ['A'], columnTypes: ['java.lang.String'],
        rows: [['x,y'], ['say "hi"'], ['line1\nline2'], [null]],
        rowCount: 4, columnCount: 1
    )

    def lines = csv.formatSuccess(result, OutputFormat.CSV, MetadataLevel.BASIC).readLines()

    assertEquals('A', lines[0])
    assertEquals('"x,y"', lines[1])
    assertEquals('"say ""hi"""', lines[2])
    assertEquals('a null renders as an empty field', '', lines.last())
  }

  @Test
  void markdownEscapesPipesInValuesAndRendersNulls() {
    def result = new QueryResult(
        columns: ['A'], columnTypes: ['java.lang.String'],
        rows: [['a|b'], [null]], rowCount: 2, columnCount: 1
    )

    def out = markdown.formatSuccess(result, OutputFormat.MARKDOWN, MetadataLevel.BASIC)

    assertTrue("a pipe in a value must be escaped", out.contains('a\\|b'))
    assertTrue(out.contains('| NULL |'))
  }

  @Test
  void markdownRightAlignsNumericColumns() {
    def out = markdown.formatSuccess(sample(), OutputFormat.MARKDOWN, MetadataLevel.BASIC)

    // Integer column right-aligned, String column left-aligned.
    assertTrue(out.contains('|---:|---|'))
  }

  @Test
  void markdownReportsAnEmptyResultExplicitly() {
    def empty = new QueryResult(
        columns: ['ID'], columnTypes: ['java.lang.Integer'],
        rows: [], rowCount: 0, columnCount: 1
    )

    def out = markdown.formatSuccess(empty, OutputFormat.MARKDOWN, MetadataLevel.BASIC)

    assertTrue(out.contains('**Row Count:** 0'))
    assertTrue(out.contains('**No rows returned**'))
  }

  @Test
  void everyFormatterReportsErrorsWithTheCodeAndDetails() {
    def failure = ValidationResult.invalid('DB_300', 'Failed to connect to database',
        'ConnectException: Connection refused')

    [csv, markdown].each { formatter ->
      def out = formatter.formatError(failure, 'SELECT 1')
      assertTrue("${formatter.class.simpleName} should report the code", out.contains('DB_300'))
      assertTrue("${formatter.class.simpleName} should report the details",
          out.contains('Connection refused'))
    }
  }

  /**
   * Column labels come from the query (SELECT x AS "a,b"), so an unescaped
   * header silently shifted every field in a CSV export.
   */
  @Test
  void csvQuotesColumnLabelsSoTheHeaderKeepsItsFieldCount() {
    def result = new QueryResult(
        columns: ['a,b', 'c'], columnTypes: ['java.lang.String', 'java.lang.String'],
        rows: [['1', '2']], rowCount: 1, columnCount: 2
    )

    def lines = csv.formatSuccess(result, OutputFormat.CSV, MetadataLevel.BASIC).readLines()

    assertEquals('"a,b",c', lines[0])
    assertEquals("header and data must agree on field count",
        lines[1].split(',').size(), 2)
  }

  @Test
  void markdownEscapesPipesInColumnLabels() {
    def result = new QueryResult(
        columns: ['a|b'], columnTypes: ['java.lang.String'],
        rows: [['v']], rowCount: 1, columnCount: 1
    )

    def out = markdown.formatSuccess(result, OutputFormat.MARKDOWN, MetadataLevel.BASIC)

    assertTrue("a pipe in a label must be escaped", out.contains('| a\\|b |'))
  }

  /**
   * The separator row used to be built from columnTypes. A driver reporting
   * fewer types than columns produced a row too short to render as a table.
   */
  @Test
  void markdownSeparatorRowAlwaysMatchesTheColumnCount() {
    def result = new QueryResult(
        columns: ['a', 'b', 'c'], columnTypes: ['java.lang.String'],
        rows: [['1', '2', '3']], rowCount: 1, columnCount: 3
    )

    def lines = markdown.formatSuccess(result, OutputFormat.MARKDOWN, MetadataLevel.BASIC)
        .readLines().findAll { it.startsWith('|') }

    assertEquals("one separator cell per column", 3,
        lines[1].split(/\|/).findAll { it }.size())
  }

  @Test
  void fullMetadataToleratesMissingColumnTypes() {
    def result = new QueryResult(
        columns: ['a', 'b'], columnTypes: ['java.lang.String'],
        rows: [['1', '2']], rowCount: 1, columnCount: 2
    )

    def csvOut = csv.formatSuccess(result, OutputFormat.CSV, MetadataLevel.FULL)
    def mdOut = markdown.formatSuccess(result, OutputFormat.MARKDOWN, MetadataLevel.FULL)

    assertFalse("should not print a null type", csvOut.contains(':null'))
    assertFalse("should not print a null type", mdOut.contains(': null'))
    assertTrue(mdOut.contains('- b: unknown'))
  }

  @Test
  void factoryResolvesEachFormatAndFallsBackToJson() {
    def factory = new FormatterFactory(json, markdown, csv)

    assertSame(json, factory.getFormatter(OutputFormat.JSON))
    assertSame(markdown, factory.getFormatter(OutputFormat.MARKDOWN))
    assertSame(csv, factory.getFormatter(OutputFormat.CSV))
    assertSame(json, factory.getFormatter('json'))
    assertSame(markdown, factory.getFormatter('markdown'))
    assertSame(csv, factory.getFormatter('csv'))
    assertSame("an unknown code must fall back to JSON", json, factory.getFormatter('nonsense'))
    assertSame("a null code must fall back to JSON", json, factory.getFormatter((String) null))
  }
}
