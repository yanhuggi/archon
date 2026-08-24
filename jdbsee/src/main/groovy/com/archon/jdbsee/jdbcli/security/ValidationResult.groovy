package com.archon.jdbsee.jdbcli.security

import groovy.transform.Immutable

/**
 * Result of a SQL security validation.
 *
 * Immutable value object. A valid result carries no error data, an invalid
 * result carries an {@link com.archon.jdbsee.jdbcli.enums.ErrorCode}
 * code plus a human readable message and details.
 */
@Immutable
final class ValidationResult {
  boolean valid
  String errorCode
  String message
  String details

  static ValidationResult valid() {
    new ValidationResult(true, null, null, null)
  }

  static ValidationResult invalid(String errorCode, String message, String details) {
    new ValidationResult(false, errorCode, message, details)
  }

  String toJsonString() {
    if (valid) {
      return '{"success":true}'
    }
    return "{\"success\":false,\"error\":{\"code\":\"$errorCode\",\"message\":\"$message\",\"details\":\"$details\"}}"
  }
}
