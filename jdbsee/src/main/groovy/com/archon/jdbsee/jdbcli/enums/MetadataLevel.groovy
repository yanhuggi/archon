package com.archon.jdbsee.jdbcli.enums

/**
 * Metadata detail level for schema exploration.
 */
enum MetadataLevel {
    BASIC, ENHANCED, FULL

    static MetadataLevel fromString(String level) {
        try {
            return valueOf(level.toUpperCase())
        } catch (IllegalArgumentException e) {
            return BASIC
        }
    }
}
