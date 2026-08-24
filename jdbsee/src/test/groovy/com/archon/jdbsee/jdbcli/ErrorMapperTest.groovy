package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import org.junit.Test

import java.sql.SQLException
import java.sql.SQLTimeoutException

import static org.junit.Assert.*

/**
 * Runtime failures must map onto the documented error codes. Before this
 * mapping existed, DB_300/DB_301/CFG_200 were defined but never emitted
 * outside batch execute, and every failure escaped as a stack trace.
 */
class ErrorMapperTest {

  @Test
  void connectionProblemsBecomeDb300() {
    assertEquals(ErrorCode.CONNECTION_FAILED,
        ErrorMapper.classify(new ConnectException('Connection refused')))
    assertEquals(ErrorCode.CONNECTION_FAILED,
        ErrorMapper.classify(new UnknownHostException('db.invalid')))
  }

  @Test
  void sqlProblemsBecomeDb301() {
    assertEquals(ErrorCode.SQL_EXECUTION_ERROR,
        ErrorMapper.classify(new SQLException('object not found: NOPE')))
  }

  @Test
  void missingDriverBecomesCfg200() {
    assertEquals(ErrorCode.DRIVER_NOT_FOUND,
        ErrorMapper.classify(new SQLException('No suitable driver found for jdbc:nosuch:')))
    assertEquals(ErrorCode.DRIVER_NOT_FOUND,
        ErrorMapper.classify(new ClassNotFoundException('org.example.Driver')))
  }

  @Test
  void timeoutBecomesDb302() {
    assertEquals(ErrorCode.TIMEOUT, ErrorMapper.classify(new SQLTimeoutException('timed out')))
  }

  @Test
  void nullDataSourceBecomesCfg202() {
    assertEquals(ErrorCode.INVALID_CONNECTION_PARAMS,
        ErrorMapper.classify(new IllegalStateException('DataSource is null. The driver may not be configured.')))
  }

  /**
   * Drivers wrap the real cause several layers deep, and the innermost link is
   * often a driver-internal type that does not extend SQLException.
   */
  @Test
  void findsTheClassifiableCauseThroughWrapperLayers() {
    def wrapped = new RuntimeException('outer',
        new IllegalStateException('cannot initialize dataSource',
            new ConnectException('Connection refused')))

    def result = ErrorMapper.toValidationResult(wrapped)

    assertFalse(result.valid)
    assertEquals(ErrorCode.CONNECTION_FAILED.code, result.errorCode)
    assertTrue("details should name the root cause", result.details.contains('ConnectException'))
  }

  @Test
  void fallsBackToTheChainWhenTheInnermostCauseIsDriverInternal() {
    // Mimics HsqlException: a driver-internal type that is not a SQLException.
    def driverInternal = new RuntimeException('unexpected token: INTO')
    def wrapped = new SQLException('statement failed', driverInternal)

    def result = ErrorMapper.toValidationResult(wrapped)

    assertEquals("should classify via the SQLException in the chain",
        ErrorCode.SQL_EXECUTION_ERROR.code, result.errorCode)
  }

  @Test
  void unrecognisedFailuresBecomeSys500() {
    def result = ErrorMapper.toValidationResult(new RuntimeException('something odd'))

    assertEquals(ErrorCode.INTERNAL_ERROR.code, result.errorCode)
    assertTrue(result.details.contains('something odd'))
  }

  @Test
  void rootCauseTerminatesOnSelfReferencingChains() {
    def selfReferencing = new RuntimeException('loop')
    // A cause pointing at itself must not spin forever.
    assertEquals('loop', ErrorMapper.rootCause(selfReferencing).message)
  }
}
