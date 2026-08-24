package com.archon.jdbsee.jdbcli

import groovy.sql.Sql
import org.flywaydb.core.Flyway
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import java.io.RandomAccessFile
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.util.concurrent.locks.ReentrantLock

class SettingsService {

  private static final Logger LOG = LoggerFactory.getLogger(SettingsService)
  ConfigService configService

  String dbFileName = 'hsql.csv'
  String dbUser = 'sa'
  String dbPassword = ''
  String dbDriver = 'org.hsqldb.jdbc.JDBCDriver'
  private FileChannel lockChannel
  private FileLock writeFileLock
  private final ReentrantLock processWriteLock = new ReentrantLock(true)

  SettingsService() {
  }

  @Inject
  SettingsService(ConfigService configService) {
    this.configService = configService
  }

  <T> T withSqlReadOnly(Closure<T> closure) {
    if (!isDatabaseInitialized()) {
      return withSqlWritable(closure)
    }
    Sql sql = null
    try {
      sql = Sql.newInstance(readOnlyDbUrl, dbUser, dbPassword, dbDriver)
      return closure(sql)
    } catch (Exception e) {
      LOG.warn('只读访问失败，降级到写路径重试: {}', e.message)
      return withSqlWritable(closure)
    } finally {
      try {
        sql?.close()
      } catch (Exception ignored) {
      }
    }
  }

  <T> T withSqlWritable(Closure<T> closure) {
    acquireWriteLock()
    try {
      Flyway flyway = new Flyway()
      flyway.setDataSource(dbUrl, dbUser, dbPassword)
      flyway.migrate()
      def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, dbDriver)
      try {
        return closure(sql)
      } finally {
        sql.close()
      }
    } finally {
      releaseWriteLock()
    }
  }

  <T> T withSql(Closure<T> closure) {
    return withSqlReadOnly(closure)
  }

  protected File getDbFile() {
    new File(configService.getUserDataDir(), dbFileName)
  }

  protected String getDbUrl() {
    "jdbc:hsqldb:file:${dbFile.absolutePath}"
  }

  protected String getReadOnlyDbUrl() {
    "jdbc:hsqldb:file:${dbFile.absolutePath};readonly=true;hsqldb.lock_file=false"
  }

  protected File getLockFile() {
    new File(configService.getUserDataDir(), '.write.lock')
  }

  private boolean isDatabaseInitialized() {
    new File("${dbFile.absolutePath}.properties").isFile()
  }

  protected void acquireWriteLock() {
    processWriteLock.lock()
    if (processWriteLock.holdCount > 1) {
      return
    }
    try {
      lockFile.parentFile?.mkdirs()
      lockChannel = new RandomAccessFile(lockFile, 'rw').channel
      writeFileLock = lockChannel.tryLock()
      if (writeFileLock == null) {
        LOG.warn('配置写入锁被占用，等待...')
        writeFileLock = lockChannel.lock()
      }
    } catch (Throwable e) {
      lockChannel?.close()
      lockChannel = null
      processWriteLock.unlock()
      throw e
    }
  }

  protected void releaseWriteLock() {
    if (!processWriteLock.isHeldByCurrentThread()) {
      return
    }
    try {
      if (processWriteLock.holdCount == 1) {
        writeFileLock?.release()
        writeFileLock = null
        lockChannel?.close()
        lockChannel = null
      }
    } finally {
      processWriteLock.unlock()
    }
  }
}
