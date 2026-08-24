package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import net.sf.jsqlparser.JSQLParserException
import net.sf.jsqlparser.parser.CCJSqlParserUtil
import net.sf.jsqlparser.statement.Statement
import net.sf.jsqlparser.statement.drop.Drop
import net.sf.jsqlparser.statement.select.Select
import net.sf.jsqlparser.statement.truncate.Truncate
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Singleton

/**
 * Validates that a SQL statement is an acceptable DML/DDL statement.
 *
 * SELECT statements are rejected (use the query command for those) and
 * destructive statements (TRUNCATE, DROP DATABASE) are blocked. Statements
 * that fail to parse are checked against a keyword blacklist before being
 * rejected.
 */
@Singleton
class DmlSecurityValidator {
  private static final Logger LOG = LoggerFactory.getLogger(DmlSecurityValidator)
  private static final List<String> INJECTION_PATTERNS = [
    '(?i);\\s*--',
    '(?i)/\\*.*?\\*/\\s*;'
  ]
  private static final List<String> BLOCKED_KEYWORDS = [
    '(?i)\\bDROP\\s+DATABASE\\b',
    '(?i)\\bTRUNCATE\\b',
    '(?i)\\bSHUTDOWN\\b'
  ]

  ValidationResult validate(String sql) {
    if (!sql?.trim()) {
      return ValidationResult.invalid(ErrorCode.SQL_PARSE_ERROR.getCode(), 'SQL statement is empty', 'Please provide a valid SQL statement')
    }

    String injectionMatch = checkInjectionPatterns(sql)
    if (injectionMatch) {
      return ValidationResult.invalid(ErrorCode.DANGEROUS_PATTERN.getCode(), 'Potentially dangerous SQL pattern detected', injectionMatch)
    }

    try {
      Statement statement = CCJSqlParserUtil.parse(sql)
      if (statement instanceof Select) {
        return ValidationResult.invalid(ErrorCode.SELECT_NOT_ALLOWED.getCode(), ErrorCode.SELECT_NOT_ALLOWED.getMessage(), "Use the 'query' command for SELECT statements")
      }
      if (statement instanceof Truncate) {
        return ValidationResult.invalid(ErrorCode.DANGEROUS_DDL.getCode(), ErrorCode.DANGEROUS_DDL.getMessage(), 'TRUNCATE TABLE is not allowed')
      }
      if (statement instanceof Drop) {
        Drop drop = (Drop) statement
        if (drop.type?.equalsIgnoreCase('DATABASE')) {
          return ValidationResult.invalid(ErrorCode.DANGEROUS_DDL.getCode(), ErrorCode.DANGEROUS_DDL.getMessage(), 'DROP DATABASE is not allowed')
        }
      }
      LOG.debug('DML validation passed: {}', sql)
      return ValidationResult.valid()
    } catch (JSQLParserException e) {
      LOG.warn('SQL parse error, falling back to keyword check: {}', e.message)
      String blockedKeyword = checkBlockedKeywords(sql)
      if (blockedKeyword) {
        return ValidationResult.invalid(ErrorCode.DANGEROUS_DDL.getCode(), ErrorCode.DANGEROUS_DDL.getMessage(), blockedKeyword)
      }
      return ValidationResult.invalid(ErrorCode.SQL_PARSE_ERROR.getCode(), 'Failed to parse SQL statement', 'SQL could not be parsed and was rejected for safety')
    }
  }

  private String checkInjectionPatterns(String sql) {
    for (String pattern : INJECTION_PATTERNS) {
      if ((sql =~ pattern).find()) {
        return 'Suspicious pattern detected'
      }
    }
    return null
  }

  private String checkBlockedKeywords(String sql) {
    for (String pattern : BLOCKED_KEYWORDS) {
      if ((sql =~ pattern).find()) {
        return 'Blocked keyword detected'
      }
    }
    return null
  }
}
