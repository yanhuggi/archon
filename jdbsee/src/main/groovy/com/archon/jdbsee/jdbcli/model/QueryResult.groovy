package com.archon.jdbsee.jdbcli.model

/**
 * Query result POJO. Rows are column-ordered: each inner list holds the values of one row,
 * aligned with {@link #columns} / {@link #columnTypes}.
 */
class QueryResult {
    List<String> columns
    List<String> columnTypes
    List<List<Object>> rows
    int rowCount
    int columnCount
    String tableName
    String schema
    String database
    Map<String, Object> metadata
    Map<String, Object> statistics
    List<Map<String, Object>> indexes

    QueryResult() {
    }

    QueryResult(List<String> columns, List<String> columnTypes, List<List<Object>> rows, int rowCount, int columnCount) {
        this.columns = columns
        this.columnTypes = columnTypes
        this.rows = rows
        this.rowCount = rowCount
        this.columnCount = columnCount
    }

    QueryResult(Map map) {
        if (map != null) {
            this.columns = map.columns as List<String>
            this.columnTypes = map.columnTypes as List<String>
            this.rows = map.rows as List<List<Object>>
            this.rowCount = (map.rowCount as Integer) ?: 0
            this.columnCount = (map.columnCount as Integer) ?: 0
            this.tableName = map.tableName as String
            this.schema = map.schema as String
            this.database = map.database as String
            this.metadata = map.metadata as Map<String, Object>
            this.statistics = map.statistics as Map<String, Object>
            this.indexes = map.indexes as List<Map<String, Object>>
        }
    }
}
