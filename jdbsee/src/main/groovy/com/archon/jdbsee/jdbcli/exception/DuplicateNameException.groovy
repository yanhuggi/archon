package com.archon.jdbsee.jdbcli.exception

import com.archon.jdbsee.jdbcli.enums.ErrorCode

/**
 * Thrown when a driver name or alias name already exists.
 */
class DuplicateNameException extends RuntimeException {
    final ErrorCode errorCode
    final String duplicateName

    DuplicateNameException(ErrorCode errorCode, String duplicateName) {
        super("${errorCode.code}: ${errorCode.message} '${duplicateName}'")
        this.errorCode = errorCode
        this.duplicateName = duplicateName
    }
}
