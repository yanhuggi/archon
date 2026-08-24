package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import org.junit.Test

import static org.junit.Assert.*

/**
 * Query validation decides from the parse tree, not the raw text. Text matching
 * used to miss writes (a comment between FOR and UPDATE) while rejecting
 * legitimate reads (the same words inside a string literal).
 */
class SqlSecurityValidatorTest {

  private final SqlSecurityValidator validator = new SqlSecurityValidator()

  @Test
  void rejectsSelectIntoBecauseItWritesOnSomeDialects() {
    ['SELECT * INTO backup FROM users',
     'SELECT id INTO b FROM t WHERE x = 1',
     'SELECT * INTO b FROM t UNION SELECT * FROM u'].each { sql ->
      def result = validator.validate(sql)
      assertFalse("should be rejected: ${sql}", result.valid)
      assertEquals(ErrorCode.INVALID_STATEMENT_TYPE.code, result.errorCode)
    }
  }

  @Test
  void rejectsForUpdateEvenWhenACommentSeparatesTheKeywords() {
    // A comment is not whitespace, so the old regex let this through.
    def result = validator.validate('SELECT id FROM t FOR/**/UPDATE')

    assertFalse(result.valid)
    assertEquals(ErrorCode.READ_ONLY_VIOLATION.code, result.errorCode)
  }

  @Test
  void findsForUpdateInsideUnionBranchesAndSubqueries() {
    ['SELECT a FROM t UNION SELECT b FROM u FOR UPDATE',
     'SELECT * FROM (SELECT a FROM t FOR UPDATE) x'].each { sql ->
      def result = validator.validate(sql)
      assertFalse("should be rejected: ${sql}", result.valid)
      assertEquals(ErrorCode.READ_ONLY_VIOLATION.code, result.errorCode)
    }
  }

  @Test
  void doesNotMistakeStringLiteralsForKeywords() {
    // Both of these were false positives under text matching.
    assertTrue(validator.validate("SELECT id FROM t WHERE note = 'ready FOR UPDATE'").valid)
    assertTrue(validator.validate("SELECT 'a; -- b' AS s FROM t").valid)
    assertTrue(validator.validate('SELECT for_update FROM t').valid)
  }

  @Test
  void rejectsStackedStatements() {
    def result = validator.validate('SELECT 1; DROP TABLE users')

    assertFalse(result.valid)
    assertEquals(ErrorCode.DANGEROUS_PATTERN.code, result.errorCode)
  }

  @Test
  void rejectsASeparatorFollowedOnlyByAComment() {
    // The parser discards the trailing comment, so this needs its own check.
    ['SELECT 1; -- injected', 'SELECT 1; /* injected */'].each { sql ->
      def result = validator.validate(sql)
      assertFalse("should be rejected: ${sql}", result.valid)
      assertEquals(ErrorCode.DANGEROUS_PATTERN.code, result.errorCode)
    }
  }

  @Test
  void allowsOrdinaryReadsIncludingTrailingSemicolonsAndComments() {
    ['SELECT id FROM users WHERE id = 1',
     'SELECT COUNT(*) FROM t',
     'SELECT id FROM t;',
     'SELECT id FROM t -- trailing note',
     'SELECT a FROM t UNION SELECT b FROM u',
     'WITH c AS (SELECT 1) SELECT * FROM c',
     'SELECT * FROM a JOIN (SELECT x FROM b) c ON a.i = c.x'].each { sql ->
      assertTrue("should be allowed: ${sql}", validator.validate(sql).valid)
    }
  }

  @Test
  void stillRejectsNonSelectAndUnparseableInput() {
    assertEquals(ErrorCode.INVALID_STATEMENT_TYPE.code,
        validator.validate('UPDATE users SET a = 1').errorCode)
    assertEquals(ErrorCode.SQL_PARSE_ERROR.code,
        validator.validate('SELECT FROM WHERE').errorCode)
    assertEquals(ErrorCode.SQL_PARSE_ERROR.code, validator.validate('   ').errorCode)
  }
}
