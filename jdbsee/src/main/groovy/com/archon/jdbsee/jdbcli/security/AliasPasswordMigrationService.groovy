package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.ConfigService
import com.archon.jdbsee.jdbcli.SettingsService
import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.exception.CryptoException
import groovy.sql.Sql
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.inject.Singleton
import java.io.File

/**
 * Migrates legacy plaintext alias passwords to encrypted storage.
 *
 * Iterates over the aliases stored in the settings database and encrypts any
 * password that is not already ciphered. Completion is recorded with a marker
 * file in the user data dir so {@link #migrateIfNeeded()} is idempotent.
 */
@Singleton
class AliasPasswordMigrationService {
  private static final Logger LOG = LoggerFactory.getLogger(AliasPasswordMigrationService)
  private static final String MARKER_FILE_NAME = '.password-migration-done'
  SettingsService settingsService
  CryptoService cryptoService

  AliasPasswordMigrationService() {
  }

  @Inject
  AliasPasswordMigrationService(SettingsService settingsService, CryptoService cryptoService) {
    this.settingsService = settingsService
    this.cryptoService = cryptoService
  }

  void setSettingsService(SettingsService settingsService) {
    this.settingsService = settingsService
  }

  void setCryptoService(CryptoService cryptoService) {
    this.cryptoService = cryptoService
  }

  void migrateIfNeeded() {
    File marker = getMigrationMarkerFile()
    if (marker?.exists()) {
      return
    }
    settingsService.withSqlWritable { Sql sql ->
      List rows = sql.rows("SELECT id, name, password FROM jdbs_aliases WHERE password IS NOT NULL AND password <> '' AND password NOT LIKE '{enc:v1}%'")
      if (rows.isEmpty()) {
        marker?.createNewFile()
        return
      }
      List<String> failed = []
      int success = 0
      rows.each { row ->
        String name = row.name as String
        try {
          String cipher = cryptoService.encrypt(row.password as String)
          sql.executeUpdate('UPDATE jdbs_aliases SET password = ? WHERE id = ?', [cipher, row.id])
          success++
        } catch (CryptoException e) {
          LOG.warn("迁移 alias '{}' 密码失败 ({}): {}", name, e.errorCode.code, e.message)
          failed << name
        } catch (Exception e) {
          LOG.error("迁移 alias '{}' 密码时发生意外错误: {}", name, e.message, e)
          failed << name
        }
      }
      if (success > 0) {
        LOG.info('已加密 {} 条历史 alias 密码', success)
      }
      if (!failed.isEmpty()) {
        LOG.error('{}: 以下 alias 密码未能迁移 -> {}', ErrorCode.MIGRATION_PARTIAL.getCode(), failed.join(', '))
      }
      if (failed.isEmpty()) {
        marker?.createNewFile()
      }
    }
  }

  private File getMigrationMarkerFile() {
    ConfigService configService = settingsService.configService
    File userDataDir = configService?.userDataDir
    if (userDataDir) {
      return new File(userDataDir, MARKER_FILE_NAME)
    }
    return null
  }
}
