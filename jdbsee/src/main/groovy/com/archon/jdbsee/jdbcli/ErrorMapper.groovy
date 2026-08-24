package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.security.ValidationResult

import java.sql.SQLException

/**
 * Maps a runtime exception onto the structured error codes the CLI documents.
 *
 * Connection problems become DB_300, SQL problems DB_301, driver/configuration
 * problems CFG_200/CFG_202 and anything unrecognised SYS_500. Without this the
 * codes exist in {@link ErrorCode} but are never emitted outside batch execute.
 */
class ErrorMapper {

  static ValidationResult toValidationResult(Throwable t) {
    Throwable root = rootCause(t)
    ErrorCode code = classify(root)
    if (code == ErrorCode.INTERNAL_ERROR) {
      // The innermost cause is often a driver-internal type that does not
      // extend SQLException (HsqlException, for one). Fall back to the nearest
      // classifiable link in the chain before calling it an internal error.
      ErrorCode fromChain = classifyChain(t)
      if (fromChain != null) {
        code = fromChain
      }
    }
    return ValidationResult.invalid(code.code, code.message, describe(root))
  }

  /** Walks the cause chain for the first link that classifies to a known code. */
  private static ErrorCode classifyChain(Throwable t) {
    Throwable current = t
    while (current != null) {
      ErrorCode code = classify(current)
      if (code != ErrorCode.INTERNAL_ERROR) {
        return code
      }
      current = (current.cause == current) ? null : current.cause
    }
    return null
  }

  static ErrorCode classify(Throwable root) {
    String type = root.class.name
    String message = root.message ?: ''

    if (root instanceof java.net.ConnectException
        || root instanceof java.net.UnknownHostException
        || root instanceof java.net.SocketTimeoutException
        || type.contains('ConnectionException')
        || type.contains('SQLTransientConnectionException')
        || type.contains('SQLNonTransientConnectionException')) {
      return ErrorCode.CONNECTION_FAILED
    }
    if (root instanceof java.sql.SQLTimeoutException) {
      return ErrorCode.TIMEOUT
    }
    if (message.toLowerCase().contains('no suitable driver')
        || type.contains('ClassNotFoundException')) {
      return ErrorCode.DRIVER_NOT_FOUND
    }
    if (root instanceof IllegalStateException && message.contains('DataSource is null')) {
      return ErrorCode.INVALID_CONNECTION_PARAMS
    }
    if (root instanceof IllegalArgumentException) {
      return ErrorCode.INVALID_CONNECTION_PARAMS
    }
    if (root instanceof SQLException || type.contains('Statement')) {
      return ErrorCode.SQL_EXECUTION_ERROR
    }
    if (root instanceof IOException) {
      return ErrorCode.IO_ERROR
    }
    return ErrorCode.INTERNAL_ERROR
  }

  /** Unwraps driver/JDBI wrapper exceptions to the cause worth reporting. */
  static Throwable rootCause(Throwable t) {
    Throwable current = t
    while (current.cause != null && current.cause != current) {
      current = current.cause
    }
    return current
  }

  private static String describe(Throwable root) {
    String message = root.message
    return message ? "${root.class.simpleName}: ${message}" : root.class.name
  }
}
