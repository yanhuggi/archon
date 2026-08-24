package com.archon.jdbsee.jdbcli.exception

import com.archon.jdbsee.jdbcli.enums.ErrorCode

/**
 * Thrown when a driver jar is not located in the expected dropins directory.
 */
class InvalidJarLocationException extends RuntimeException {
    final ErrorCode errorCode
    final File jarFile

    InvalidJarLocationException(ErrorCode errorCode, File jarFile) {
        super("${errorCode.code}: ${errorCode.message} '${jarFile?.absolutePath}'")
        this.errorCode = errorCode
        this.jarFile = jarFile
    }
}
