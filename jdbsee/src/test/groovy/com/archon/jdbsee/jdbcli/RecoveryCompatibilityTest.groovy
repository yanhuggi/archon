package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.config.JdbsAlias
import com.archon.jdbsee.jdbcli.mcp.McpModule
import com.archon.jdbsee.jdbcli.model.QueryResult
import org.junit.Test
import org.jdbi.v3.core.statement.Query
import picocli.CommandLine

import java.lang.reflect.Modifier

import static org.junit.Assert.*

class RecoveryCompatibilityTest {

  @Test
  void recoveredGroovyPropertiesRetainTheProductAccessors() {
    assertPropertyAccessors(AbstractDbCommand, 'alias', String)
    assertPropertyAccessors(DriverManagerDataSource, 'url', String)
    assertPropertyAccessors(QueryCommand, 'sqlText', String)
    assertPropertyAccessors(ExecuteCommand, 'sqlFile', File)
    assertPropertyAccessors(JdbsAlias, 'remark', String)
    assertPropertyAccessors(QueryResult, 'rowCount', Integer.TYPE)
  }

  @Test
  void queryCallbackAndCompatibilityModuleMatchTheProductContract() {
    def callbackMethod = QueryCallback.getMethod('doWithQuery', Query)
    assertArrayEquals([Throwable] as Class<?>[], callbackMethod.exceptionTypes)
    assertNotNull(McpModule.getAnnotation(dagger.Module))
  }

  @Test
  void aliasAndDriverCommandsExposeHelpAtEveryLevel() {
    assertHelpOption(new CommandLine(new AliasCommand()))
    assertHelpOption(new CommandLine(new DriverCommand()))
  }

  private static void assertPropertyAccessors(Class<?> type, String property, Class<?> propertyType) {
    def suffix = property[0].toUpperCase() + property.substring(1)
    assertNotNull(type.getMethod("get${suffix}"))
    assertNotNull(type.getMethod("set${suffix}", propertyType))
    assertFalse("${type.simpleName}.${property} must not be a public field",
        Modifier.isPublic(type.getDeclaredField(property).modifiers))
  }

  private static void assertHelpOption(CommandLine commandLine) {
    def commands = [commandLine] + commandLine.subcommands.values()
    commands.each { command ->
      assertTrue("${command.commandName} is missing --help",
          command.commandSpec.options().any { '--help' in it.names() })
    }
  }
}
