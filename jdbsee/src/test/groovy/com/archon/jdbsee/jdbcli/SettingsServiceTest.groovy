package com.archon.jdbsee.jdbcli

import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

import static org.junit.Assert.*

class SettingsServiceTest {

  @Rule
  public TemporaryFolder temporaryFolder = new TemporaryFolder()

  @Test
  void readOnlyAccessOnAFreshHomeInitializesTheDatabase() {
    File userFolder = temporaryFolder.newFolder('fresh-home')
    def settings = new SettingsService(configFor(userFolder))

    def tableCount = settings.withSqlReadOnly { sql ->
      sql.firstRow("SELECT COUNT(*) AS C FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'JDBS_DRIVERS'").C
    }

    assertEquals(1, tableCount as int)
    assertTrue(new File(userFolder, 'data').isDirectory())
    assertTrue(new File(userFolder, 'data/.write.lock').isFile())
  }

  @Test
  void nestedWritesReleaseTheOuterProcessAndFileLocks() {
    File userFolder = temporaryFolder.newFolder('nested-writes')
    def settings = new SettingsService(configFor(userFolder))

    settings.withSqlWritable { outerSql ->
      assertNotNull(outerSql)
      settings.withSqlWritable { innerSql ->
        assertEquals(1, innerSql.firstRow('VALUES (1)').getAt(0) as int)
      }
    }

    def lockField = SettingsService.getDeclaredField('lockChannel')
    lockField.accessible = true
    assertNull(lockField.get(settings))

    assertEquals(1, settings.withSqlWritable { sql -> sql.firstRow('VALUES (1)').getAt(0) } as int)
  }

  private static ConfigService configFor(File userFolder) {
    new ConfigService() {
      @Override
      File getUserFolder() {
        userFolder
      }

      @Override
      File getUserDataDir() {
        new File(userFolder, 'data')
      }
    }
  }
}
