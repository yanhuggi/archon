package com.archon.jdbsee.jdbcli.enums

/**
 * Central error codes for the JDBSee CLI, grouped by domain prefix:
 * SQL_* validation, CFG_* configuration, DB_* runtime, SEC_* security, SYS_* system.
 */
enum ErrorCode {
    INVALID_STATEMENT_TYPE("SQL_100", "Only SELECT statements are allowed"),
    DANGEROUS_PATTERN("SQL_101", "Potentially dangerous SQL pattern detected"),
    READ_ONLY_VIOLATION("SQL_102", "SELECT FOR UPDATE is not allowed"),
    SQL_PARSE_ERROR("SQL_103", "Failed to parse SQL statement"),
    DANGEROUS_DDL("SQL_110", "Dangerous DDL operation is blocked"),
    SELECT_NOT_ALLOWED("SQL_111", "Use 'query' command for SELECT statements"),
    DRIVER_NOT_FOUND("CFG_200", "JDBC driver not found"),
    ALIAS_NOT_FOUND("CFG_201", "Database alias not found"),
    INVALID_CONNECTION_PARAMS("CFG_202", "Invalid connection parameters"),
    DUPLICATE_DRIVER_NAME("CFG_203", "Driver name already exists"),
    DUPLICATE_ALIAS_NAME("CFG_204", "Alias name already exists"),
    INVALID_JAR_LOCATION("CFG_205", "Jar file must be located in the dropins directory"),
    INVALID_JAR_DRIVER_DIR("CFG_206", "Jar file must be located in dropins/<driver-name>/ directory"),
    DRIVER_DIR_NOT_FOUND("CFG_207", "Driver directory does not exist in dropins"),
    CONNECTION_FAILED("DB_300", "Failed to connect to database"),
    SQL_EXECUTION_ERROR("DB_301", "SQL execution error"),
    TIMEOUT("DB_302", "Query execution timeout"),
    KEY_FILE_CORRUPTED("SEC_400", "Master key file is corrupted (expected 32 bytes)"),
    KEY_FILE_UNREADABLE("SEC_401", "Master key file is unreadable"),
    DECRYPT_FAILED("SEC_402", "Failed to decrypt password"),
    ENCRYPT_FAILED("SEC_403", "Failed to encrypt password"),
    MIGRATION_PARTIAL("SEC_404", "Some alias passwords could not be migrated to encrypted format"),
    INTERNAL_ERROR("SYS_500", "Internal system error"),
    IO_ERROR("SYS_501", "File I/O error")

    final String code
    final String message

    ErrorCode(String code, String message) {
        this.code = code
        this.message = message
    }
}
