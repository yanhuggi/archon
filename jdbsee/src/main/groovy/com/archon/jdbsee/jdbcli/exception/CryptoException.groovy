package com.archon.jdbsee.jdbcli.exception

import com.archon.jdbsee.jdbcli.enums.ErrorCode

/**
 * Thrown when encryption or decryption of alias passwords fails.
 */
class CryptoException extends RuntimeException {
    final ErrorCode errorCode

    CryptoException(ErrorCode errorCode) {
        super("${errorCode.code}: ${errorCode.message}")
        this.errorCode = errorCode
    }

    CryptoException(ErrorCode errorCode, String detail) {
        super(detail ? "${errorCode.code}: ${errorCode.message} - $detail" : "${errorCode.code}: ${errorCode.message}")
        this.errorCode = errorCode
    }

    CryptoException(ErrorCode errorCode, String detail, Throwable cause) {
        super(detail ? "${errorCode.code}: ${errorCode.message} - $detail" : "${errorCode.code}: ${errorCode.message}", cause)
        this.errorCode = errorCode
    }
}
