package com.archon.jdbsee.jdbcli.config

import com.archon.jdbsee.jdbcli.SettingsService
import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.exception.CryptoException
import com.archon.jdbsee.jdbcli.exception.DuplicateNameException
import com.archon.jdbsee.jdbcli.security.CryptoService
import groovy.sql.GroovyRowResult
import groovy.sql.Sql
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject

/**
 * Persists {@link JdbsAlias alias} settings.
 */
class JdbsAliasDao {

  private static final Logger LOG = LoggerFactory.getLogger(JdbsAliasDao)
  SettingsService settingsService
  JdbsDriverDao driverDao
  CryptoService cryptoService

  JdbsAliasDao() {
  }

  @Inject
  JdbsAliasDao(
      SettingsService settingsService,
      JdbsDriverDao driverDao,
      CryptoService cryptoService
  ) {
    this.settingsService = settingsService
    this.driverDao = driverDao
    this.cryptoService = cryptoService
  }

  void setCryptoService(CryptoService cryptoService) {
    this.cryptoService = cryptoService
  }

  long insert(
      final String driver,
      final String name,
      final String url,
      final String username,
      final String password,
      final String remark
  ) {
    Long aliasId
    def storedPassword = encryptForStorage(password)
    withSqlWritable { Sql sql ->
      sql.cacheConnection {
        sql.withTransaction {
          def existing = sql.firstRow(name: name, 'SELECT id FROM jdbs_aliases WHERE name = :name')
          if (existing) {
            throw new DuplicateNameException(ErrorCode.DUPLICATE_ALIAS_NAME, name)
          }
          def driverId
          try {
            driverId = Long.valueOf(driver)
          } catch (NumberFormatException e) {
            driverId = driverDao.findDriverByName(driver).get().id
          }
          aliasId = sql.executeInsert(
              name: name, driverId: driverId, url: url, username: username, password: storedPassword, remark: remark,
              """\
                        INSERT INTO jdbs_aliases
                          (name, driver_id, url, username, password, remark)
                        VALUES
                          (:name, :driverId, :url, :username, :password, :remark)
                      """).flatten().first() as Long
        }
      }
    }
    return aliasId
  }

  int delete(final String... names) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate(names: names,
          """\
                    DELETE FROM jdbs_aliases
                    WHERE
                      name IN (${names.collect{"'$it'"}.join(',')})
                  """ as String)
    }
  }

  int delete(final Long... ids) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate(
          """\
                    DELETE FROM jdbs_aliases
                    WHERE
                      id IN (${ids.join ', '})
                  """ as String)
    }
  }

  List<JdbsAlias> listAliases() {
    withSqlReadOnly { Sql sql ->
      sql.rows("""
          SELECT
            als.id, als.name, als.url, als.username, als.password, als.remark,
            drv.id AS driver_id, drv.name AS driver_name,
            drv.clazz AS driver_clazz, drv.clazz_expr AS driver_expr
          FROM
            jdbs_aliases als INNER JOIN jdbs_drivers drv ON (als.driver_id=drv.id)
          ORDER BY name ASC"""
      ).collect { row ->
        rowToBean(row)
      }
    }
  }

  Optional<JdbsAliasDetails> findAliasByName(final String name) {
    getAliasDetails { Sql sql -> sql.firstRow(name: name, 'SELECT * FROM jdbs_aliases WHERE name = :name') }
  }

  Optional<JdbsAliasDetails> findAliasById(final Long driverId) {
    getAliasDetails { Sql sql -> sql.firstRow(driverId: driverId, 'SELECT * FROM jdbs_aliases WHERE id = :driverId') }
  }

  Optional<JdbsAliasDetails> findAlias(final String aliasRef) {
    try {
      findAliasById(Long.valueOf(aliasRef))
    } catch (final NumberFormatException e) {
      findAliasByName(aliasRef)
    }
  }

  Optional<JdbsAliasDetails> getAliasDetails(final Closure<GroovyRowResult> aliasRowClosure) {
    withSqlReadOnly { Sql sql ->
      GroovyRowResult aliasRow = aliasRowClosure(sql)
      JdbsAliasDetails result
      if (aliasRow) {
        Long aliasId = aliasRow.id as Long

        result = new JdbsAliasDetails(
            id: aliasId,
            driverId: aliasRow.driver_id as Long,
            name: aliasRow.name as String,
            url: aliasRow.url as String,
            username: aliasRow.username as String,
            password: decryptPassword(aliasRow.password as String, aliasRow.name as String),
            remark: aliasRow.remark as String,
            driverDetails: driverDao.findDriverById(aliasRow.driver_id as Long).get()
        )
        result.driver = result.driverDetails
      } else {
        result = null
      }

      return Optional.ofNullable(result)
    }
  }

  protected String encryptForStorage(String password) {
    if (password == null || password.isEmpty()) {
      return password
    }
    if (cryptoService == null) {
      return password
    }
    return cryptoService.encrypt(password)
  }

  protected String decryptPassword(String stored, String aliasName) {
    if (stored == null || stored.isEmpty()) {
      return stored
    }
    if (!CryptoService.isCiphered(stored)) {
      return stored
    }
    if (cryptoService == null) {
      return stored
    }
    try {
      return cryptoService.decrypt(stored)
    } catch (CryptoException e) {
      LOG.warn("alias '{}' 密码解密失败 ({}): {}", aliasName, e.errorCode.code, e.message)
      return ''
    }
  }

  <T> T withSqlReadOnly(final Closure<T> closure) {
    settingsService.withSqlReadOnly { Sql sql ->
      closure(sql)
    }
  }

  <T> T withSqlWritable(final Closure<T> closure) {
    settingsService.withSqlWritable { Sql sql ->
      closure(sql)
    }
  }

  <T> T withSql(final Closure<T> closure) {
    settingsService.withSql { Sql sql ->
      closure(sql)
    }
  }

  protected JdbsAlias rowToBean(final GroovyRowResult row) {
    row == null ?
        null :
        new JdbsAlias(
            id: row.id as Long,
            name: row.name as String,
            url: row.url as String,
            username: row.username as String,
            password: decryptPassword(row.password as String, row.name as String),
            remark: row.remark as String,
            driverId: row.driver_id as Long,
            driver: new JdbsDriver(
                id: row.driver_id as Long,
                name: row.driver_name as String,
                driverClass: row.driver_clazz as String,
                driverClassExpr: row.driver_expr as String
            )
        )
  }
}
