package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.format.FormatterFactory
import com.archon.jdbsee.jdbcli.security.DmlSecurityValidator
import groovy.json.JsonSlurper
import org.junit.Test

import static org.junit.Assert.*

/**
 * Statement splitting tears a CREATE TRIGGER ... BEGIN a; b; END body into
 * fragments, which would run the inner statements standalone against the
 * tables. Such input must be refused before anything executes.
 */
class ExecuteServiceBlockBodyTest {

  private final ExecuteService service =
      new ExecuteService(new DmlSecurityValidator(), new FormatterFactory())

  @Test
  void detectsATriggerBodyThatSplittingWouldTearApart() {
    def statements = service.splitSqlStatements(
        'CREATE TRIGGER x AFTER INSERT ON a BEGIN UPDATE b SET c=1; UPDATE d SET e=2; END')

    assertEquals("the body is split, which is the problem", 3, statements.size())
    assertTrue(ExecuteService.hasSplitBlockBody(statements))
  }

  @Test
  void leavesIntactBodiesAlone() {
    // jsqlparser keeps these whole, so there is nothing to refuse.
    [
        'CREATE PROCEDURE p() BEGIN INSERT INTO a VALUES (1); DELETE FROM b; END',
        'CREATE FUNCTION f() RETURNS INT BEGIN DECLARE x INT; RETURN 1; END',
        'BEGIN UPDATE a SET b=1; UPDATE c SET d=2; END'
    ].each { sql ->
      def statements = service.splitSqlStatements(sql)
      assertFalse("should not be refused: ${sql}", ExecuteService.hasSplitBlockBody(statements))
    }
  }

  @Test
  void doesNotRefuseOrdinaryStatements() {
    [
        'INSERT INTO t VALUES (1)',
        'INSERT INTO t VALUES (1); INSERT INTO t VALUES (2)',
        'CREATE TABLE a (i INT); CREATE TABLE b (j INT); CREATE INDEX x ON a(i)',
        "INSERT INTO t(a) VALUES ('x;y')",
        'INSERT INTO t(begin_date) VALUES (1); INSERT INTO t(end_date) VALUES (2)'
    ].each { sql ->
      def statements = service.splitSqlStatements(sql)
      assertFalse("should not be refused: ${sql}", ExecuteService.hasSplitBlockBody(statements))
    }
  }

  @Test
  void refusesWithoutOpeningAConnection() {
    // A null DataSource proves nothing was executed: reaching the database
    // would throw instead of returning a formatted refusal.
    def outcome = service.executeBatchWithOutcome(null,
        'CREATE TRIGGER x AFTER INSERT ON a BEGIN UPDATE b SET c=1; UPDATE d SET e=2; END',
        'json')

    assertTrue(outcome.failed)
    def parsed = new JsonSlurper().parseText(outcome.output)
    assertFalse(parsed.success)
    assertEquals('SQL_110', parsed.error.code)
    assertTrue(parsed.error.details.contains('BEGIN'))
  }

  @Test
  void aLoneEndFragmentIsTreatedAsASplitBody() {
    assertTrue(ExecuteService.hasSplitBlockBody(['UPDATE a SET b=1', 'END']))
    assertTrue(ExecuteService.hasSplitBlockBody(['UPDATE a SET b=1', 'end;']))
  }

  @Test
  void aSingleStatementIsNeverTreatedAsSplit() {
    assertFalse(ExecuteService.hasSplitBlockBody(['END']))
    assertFalse(ExecuteService.hasSplitBlockBody([]))
  }
}
