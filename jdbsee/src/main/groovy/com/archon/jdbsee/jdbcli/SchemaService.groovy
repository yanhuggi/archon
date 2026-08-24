package com.archon.jdbsee.jdbcli

import com.google.common.collect.ArrayTable
import com.google.common.collect.Table
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import java.sql.ResultSet

class SchemaService extends AbstractDbCommandService {

  private static final Logger LOG = LoggerFactory.getLogger(SchemaService)

  @Inject
  public ConsoleService consoleService
  @Inject
  public QueryService queryService

  @Inject
  SchemaService() {
  }

  void run(SchemaCommand.SchemaDriverCommand cmd) {
    cmd.with {
      doRun(cmd) { dataSource ->
        def connection = dataSource.connection
        try {
          def dbMeta = connection.metaData
          if (matches) {
            if (!dbMeta.properties.containsKey(matches)) {
              return
            }
            def propValue = dbMeta.properties[matches]
            if (propValue instanceof ResultSet) {
              consoleService.printResultSet(propValue)
            } else if (propValue instanceof Iterable) {
              def table = ArrayTable.create([matches], ['Property', 'Value'])
              table.put(matches, 'Property', matches)
              table.put(matches, 'Value', propValue.collect { it as String })
              consoleService.renderTable(table, getWidth(cmd))
            } else {
              def table = ArrayTable.create([matches], ['Property', 'Value'])
              table.put(matches, 'Property', matches)
              table.put(matches, 'Value', propValue as String)
              consoleService.renderTable(table, getWidth(cmd))
            }
          } else {
            def table = ArrayTable.create(dbMeta.properties.keySet(), ['key', 'value'])
            dbMeta.properties.with { props ->
              props.each { k, v ->
                table.put(k, 'key', k)
                def valueString = v.with {
                  if (it instanceof ResultSet) {
                    toString(it)
                  } else if (it instanceof Iterable) {
                    it.collect { it as String }
                  } else {
                    it as String
                  }
                }
                table.put(k, 'value', valueString)
              }
            }
            consoleService.renderTable(table, getWidth(cmd))
          }
        } finally {
          connection.close()
        }
      }
    }
  }

  void run(SchemaCommand.SchemaViewsCommand cmd) {
    describeTables(cmd, cmd.matches, 'VIEW')
  }

  void run(SchemaCommand.SchemaTablesCommand cmd) {
    describeTables(cmd, cmd.matches, 'TABLE')
  }

  void describeTables(AbstractDbCommand cmd, String matches, String... types) {
    doRun(cmd) { dataSource ->
      def connection = dataSource.connection
      try {
        def table = connection.metaData.getTables(null, null, matches, types).with { rs ->
          def rows = []
          def header = [key: 'header', catalog: 'CATALOG', schema: 'SCHEMA', name: 'NAME']
          rows << header
          Integer count = 0
          while (rs.next()) {
            count = count.next()
            rows << [key: count as String, catalog: rs.getObject(1), schema: rs.getObject(2), name: rs.getObject(3)]
          }
          def table = ArrayTable.create(rows.collect { it.key }, header.keySet() - 'key')
          if (rows) {
            rows.each { row ->
              row.each { k, v ->
                if (k != 'key') {
                  table.put(row.key, k, v ?: '-')
                }
              }
            }
          }
          table
        }
        consoleService.renderTable(table, getWidth(cmd))
      } finally {
        connection.close()
      }
    }
  }

  String toString(ResultSet resultSet) {
    toList(resultSet) as String
  }

  List<Map<String, Object>> toList(ResultSet resultSet) {
    resultSet.with { rs ->
      rs.metaData.with { md ->
        def rows = new ArrayList()
        while (rs.next()) {
          def row = new HashMap(md.columnCount)
          for (int i = 1; i <= md.columnCount; i++) {
            row[md.getColumnName(i)] = rs.getObject(i)
          }
          rows << row
        }
        rows
      }
    }
  }
}
