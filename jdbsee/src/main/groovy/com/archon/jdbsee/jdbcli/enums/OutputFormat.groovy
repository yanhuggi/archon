package com.archon.jdbsee.jdbcli.enums

/**
 * Supported output formats for query results.
 */
enum OutputFormat {
    JSON("json", "Structured JSON output"),
    JSON_PRETTY("json-pretty", "Pretty-printed JSON output (legacy)"),
    MARKDOWN("markdown", "Markdown table with metadata"),
    CSV("csv", "CSV format")

    final String code
    final String description

    OutputFormat(String code, String description) {
        this.code = code
        this.description = description
    }

    static OutputFormat fromCode(String code) {
        if (code == null) {
            return JSON
        }
        String normalizedCode = code.trim().toLowerCase()
        return values().find { it.code == normalizedCode } ?: JSON
    }
}
