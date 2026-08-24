package com.archon.jdbsee.jdbcli.config

import com.archon.jdbsee.jdbcli.ConfigService
import com.archon.jdbsee.jdbcli.SettingsService
import com.archon.jdbsee.jdbcli.security.CryptoService
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

import static org.junit.Assert.*

class DaoRoundTripTest {

  @Rule
  public TemporaryFolder temporaryFolder = new TemporaryFolder()

  @Test
  void driverAndEncryptedAliasRoundTripThroughTheRecoveredSchema() {
    File userFolder = temporaryFolder.newFolder('dao-home')
    ConfigService config = configFor(userFolder)
    def settings = new SettingsService(config)
    def driverDao = new JdbsDriverDao(settings)
    driverDao.configService = config
    def crypto = new CryptoService(config)
    def aliasDao = new JdbsAliasDao(settings, driverDao, crypto)

    long driverId = driverDao.insert('hsql', 'org.hsqldb.jdbc.JDBCDriver', '(.*)Driver(.*)', [], [])
    long aliasId = aliasDao.insert(driverId.toString(), 'local', 'jdbc:hsqldb:mem:test', 'sa', 'secret', 'recovered')

    assertTrue(driverId >= 0)
    assertTrue(aliasId >= 0)
    def alias = aliasDao.findAliasByName('local').get()
    assertEquals('secret', alias.password)
    assertEquals('recovered', alias.remark)
    assertEquals('hsql', alias.driver.name)

    String stored = settings.withSqlReadOnly { sql ->
      sql.firstRow(name: 'local', 'SELECT password FROM jdbs_aliases WHERE name = :name').password
    }
    assertTrue(CryptoService.isCiphered(stored))
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
