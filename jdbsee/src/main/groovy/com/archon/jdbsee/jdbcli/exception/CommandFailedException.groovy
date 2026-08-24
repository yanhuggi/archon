package com.archon.jdbsee.jdbcli.exception

/**
 * Signals that a command failed and has already reported the failure in its
 * configured output format.
 *
 * The top level uses this to set a non-zero exit status without printing a
 * second, redundant stack trace over the structured error.
 */
class CommandFailedException extends RuntimeException {

  CommandFailedException(Throwable cause) {
    super(cause?.message, cause)
  }
}
