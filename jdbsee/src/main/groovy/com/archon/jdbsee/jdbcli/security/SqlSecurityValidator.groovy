package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import net.sf.jsqlparser.JSQLParserException
import net.sf.jsqlparser.parser.CCJSqlParserUtil
import net.sf.jsqlparser.statement.Statement
import net.sf.jsqlparser.statement.Statements
import net.sf.jsqlparser.statement.select.PlainSelect
import net.sf.jsqlparser.statement.select.Select
import net.sf.jsqlparser.statement.select.SelectBody
import net.sf.jsqlparser.statement.select.SetOperationList
import net.sf.jsqlparser.statement.select.SubSelect
import net.sf.jsqlparser.statement.select.WithItem
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Singleton

/**
 * Validates that a SQL statement is a safe read-only SELECT.
 *
 * Rejects anything that is not a plain SELECT: parse failures, non-SELECT
 * statement types, SELECT ... FOR UPDATE, SELECT ... INTO and multi-statement
 * input.
 *
 * Decisions are taken from the parse tree rather than by matching the raw text.
 * Text matching both missed writes (a comment between FOR and UPDATE) and
 * rejected legitimate reads (the words appearing inside a string literal).
 */
@Singleton
class SqlSecurityValidator {
  private static final Logger LOG = LoggerFactory.getLogger(SqlSecurityValidator)

  ValidationResult validate(String sql) {
    if (!sql?.trim()) {
      return ValidationResult.invalid(ErrorCode.SQL_PARSE_ERROR.getCode(), 'SQL statement is empty', 'Please provide a valid SQL statement')
    }

    try {
      if (countStatements(sql) > 1) {
        return ValidationResult.invalid(ErrorCode.DANGEROUS_PATTERN.getCode(),
            'Only a single SELECT statement is allowed',
            'The input contains more than one statement')
      }
      if (endsWithCommentedOutTail(sql)) {
        return ValidationResult.invalid(ErrorCode.DANGEROUS_PATTERN.getCode(),
            'Possible SQL injection detected',
            'A statement separator followed only by a comment is a common injection tail')
      }

      Statement statement = CCJSqlParserUtil.parse(sql)
      if (!(statement instanceof Select)) {
        return ValidationResult.invalid(ErrorCode.INVALID_STATEMENT_TYPE.getCode(), 'Only SELECT statements are allowed', "Detected statement type: ${statement.class.simpleName}")
      }

      Select select = (Select) statement
      if (hasIntoClause(select)) {
        return ValidationResult.invalid(ErrorCode.INVALID_STATEMENT_TYPE.getCode(),
            'SELECT ... INTO is not allowed',
            'SELECT ... INTO creates or writes a table on some dialects; use the execute command for writes')
      }
      if (hasForUpdate(select)) {
        return ValidationResult.invalid(ErrorCode.READ_ONLY_VIOLATION.getCode(), 'SELECT FOR UPDATE is not allowed', 'Use a plain SELECT statement instead')
      }
      LOG.debug('SQL validation passed: {}', sql)
      return ValidationResult.valid()
    } catch (JSQLParserException e) {
      LOG.warn('SQL parse error: {}', e.message)
      return ValidationResult.invalid(ErrorCode.SQL_PARSE_ERROR.getCode(), 'Failed to parse SQL statement', e.message)
    }
  }

  /**
   * Detects a statement separator whose remainder is entirely commented out —
   * the classic "'; --" injection tail. The parser discards such a trailing
   * comment, so it would otherwise pass unnoticed.
   *
   * Semicolons inside string literals are skipped, so a legitimate
   * SELECT 'a; -- b' is not affected.
   */
  private boolean endsWithCommentedOutTail(String sql) {
    int cut = indexOfUnquotedSemicolon(sql)
    if (cut < 0) {
      return false
    }
    String tail = sql.substring(cut + 1).trim()
    if (!tail) {
      return false
    }
    return tail.startsWith('--') || tail.startsWith('/*') || tail.startsWith('#')
  }

  /** Index of the first semicolon outside a single- or double-quoted literal. */
  private int indexOfUnquotedSemicolon(String sql) {
    boolean inSingle = false
    boolean inDouble = false
    for (int i = 0; i < sql.length(); i++) {
      char c = sql.charAt(i)
      if (c == ('\'' as char) && !inDouble) {
        inSingle = !inSingle
      } else if (c == ('"' as char) && !inSingle) {
        inDouble = !inDouble
      } else if (c == (';' as char) && !inSingle && !inDouble) {
        return i
      }
    }
    return -1
  }

  /**
   * Counts top-level statements so that stacked input is rejected as such
   * rather than reported as a parse failure.
   */
  private int countStatements(String sql) {
    try {
      Statements statements = CCJSqlParserUtil.parseStatements(sql)
      return statements?.statements?.size() ?: 1
    } catch (Exception ignored) {
      // Let the single-statement parse below produce the real diagnostic.
      return 1
    }
  }

  private boolean hasIntoClause(Select select) {
    return anyPlainSelect(select) { PlainSelect plain -> plain.intoTables }
  }

  private boolean hasForUpdate(Select select) {
    return anyPlainSelect(select) { PlainSelect plain -> plain.isForUpdate() }
  }

  /** Applies the test to every PlainSelect reachable from the statement. */
  private boolean anyPlainSelect(Select select, Closure<Boolean> test) {
    boolean found = walkBody(select.selectBody, test)
    if (found) {
      return true
    }
    for (WithItem item : (select.withItemsList ?: [])) {
      // A CTE body is reachable through its SubSelect, not a SelectBody.
      SubSelect sub = item.subSelect
      if (sub != null && walkBody(sub.selectBody, test)) {
        return true
      }
    }
    return false
  }

  private boolean walkBody(SelectBody body, Closure<Boolean> test) {
    if (body instanceof PlainSelect) {
      PlainSelect plain = (PlainSelect) body
      if (test(plain)) {
        return true
      }
      // A subquery in FROM can carry its own FOR UPDATE or INTO.
      if (plain.fromItem instanceof SubSelect
          && walkBody(((SubSelect) plain.fromItem).selectBody, test)) {
        return true
      }
      for (def join : (plain.joins ?: [])) {
        if (join.rightItem instanceof SubSelect
            && walkBody(((SubSelect) join.rightItem).selectBody, test)) {
          return true
        }
      }
      return false
    }
    if (body instanceof SetOperationList) {
      return ((SetOperationList) body).selects.any { walkBody(it, test) }
    }
    return false
  }
}
