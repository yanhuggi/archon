package com.archon.jdbsee.jdbcli.config

import com.archon.jdbsee.jdbcli.ConfigService
import com.archon.jdbsee.jdbcli.SettingsService
import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.exception.InvalidJarLocationException
import groovy.sql.GroovyResultSet
import groovy.sql.GroovyRowResult
import groovy.sql.Sql

import javax.inject.Inject

/**
 * Persists {@link JdbsDriver driver} settings.
 */
class JdbsDriverDao {
  SettingsService settingsService
  @Inject
  public ConfigService configService

  JdbsDriverDao() {
  }

  @Inject
  JdbsDriverDao(
      SettingsService settingsService
  ) {
    this.settingsService = settingsService
  }

  long insert(
      final String name,
      final String driverClass,
      final String driverClassExpr,
      final List<File> jars,
      final List<String> deps) {
    Long driverId
    withSqlWritable { Sql sql ->
      sql.cacheConnection {
        sql.withTransaction {
          driverId = sql.executeInsert(
              """\
                        INSERT INTO jdbs_drivers
                          (name, clazz, clazz_expr)
                        VALUES
                          (:name, :clazz, :clazz_expr)
                      """, name: name, clazz: driverClass, clazzExpr: driverClassExpr).flatten().first() as Long

          jars?.each { File jarFile ->
            addJar sql, driverId, jarFile
          }

          deps?.each { String dependency ->
            addDependency sql, driverId, dependency
          }
        }
      }
    }
    return driverId
  }

  int delete(final String... names) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate(names: names,
          """\
                    DELETE FROM jdbs_drivers
                    WHERE
                      name IN (${names.collect{"'$it'"}.join(',')})
                  """ as String)
    }
  }

  int delete(final Long... ids) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate(
          """\
                    DELETE FROM jdbs_drivers
                    WHERE
                      id IN (${ids.join ', '})
                  """ as String)
    }
  }

  List<JdbsDriver> listDrivers() {
    withSqlReadOnly { Sql sql ->
      sql.rows('SELECT * FROM jdbs_drivers ORDER BY name ASC').collect { row ->
        rowToBean(row)
      }
    }
  }

  Optional<JdbsDriverDetails> findDriverByName(final String name) {
    getDriverDetails { Sql sql -> sql.firstRow(name: name,
        '''
                SELECT
                  id, name, clazz, clazz_expr
                FROM jdbs_drivers
                WHERE
                  name = :name
            ''')
    }
  }

  Optional<JdbsDriverDetails> findDriverById(final Long driverId) {
    getDriverDetails { Sql sql -> sql.firstRow(driverId: driverId,
        '''
                SELECT
                  id, name, clazz, clazz_expr
                FROM jdbs_drivers
                WHERE
                  id = :driverId
            ''')
    }
  }

  Optional<JdbsDriverDetails> getDriverDetails(final Closure<GroovyRowResult> drvRowClosure) {
    withSqlReadOnly { Sql sql ->
      GroovyRowResult driverRow = drvRowClosure(sql)
      JdbsDriverDetails result
      if (driverRow) {
        Long driverId = driverRow.id as Long

        result = new JdbsDriverDetails(
            id: driverId,
            name: driverRow.name as String,
            driverClass: driverRow.clazz as String,
            driverClassExpr: driverRow.clazz_expr as String,
            jars: [], deps: []
        )

        result.with {
          sql.eachRow(driverId: driverId, '''
                        SELECT
                          id, driver_id, path
                        FROM jdbs_jars
                        WHERE
                          driver_id = :driverId
                        ORDER BY id
                        '''
          ) { GroovyResultSet row ->
            jars << new JdbsJar(
                id: row['id'] as Long,
                driverId: row['driver_id'] as Long,
                file: new File(row['path'] as String)
            )
          }

          sql.eachRow(driverId: driverId, '''
                        SELECT
                          id, driver_id, gav
                        FROM jdbs_deps
                        WHERE
                          driver_id = :driverId
                        ORDER BY id
                        '''
          ) { GroovyResultSet row ->
            deps << new JdbsDep(
                id: row['id'] as Long,
                driverId: row['driver_id'] as Long,
                gav: row['gav'] as String
            )
          }
        }
      } else {
        result = null
      }

      return Optional.ofNullable(result)
    }
  }

  long addJar(final Sql sql, final Long driverId, final File jarFile) {
    sql.executeInsert(driver_id: driverId, path: jarFile.absolutePath, 'INSERT INTO jdbs_jars (driver_id, path) VALUES (:driver_id, :path)').flatten().first() as Long
  }

  List<Long> addJar(final Long driverId, final File... jarFiles) {
    withSqlWritable { Sql sql ->
      jarFiles.collect { File jarFile ->
        addJar sql, driverId, jarFile
      }
    }
  }

  int removeJar(final Long... jarIds) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate("""DELETE FROM jdbs_jars WHERE id IN (${jarIds.join ', '})""" as String)
    }
  }

  long addDependency(final Sql sql, final Long driverId, final String dependency) {
    sql.executeInsert(driver_id: driverId, gav: dependency, 'INSERT INTO jdbs_deps (driver_id, gav) VALUES (:driver_id, :gav)').flatten().first() as Long
  }

  List<Long> addDependency(final Long driverId, final String... dependencies) {
    withSqlWritable { Sql sql ->
      dependencies.collect { String dependency ->
        addDependency sql, driverId, dependency
      }
    }
  }

  int removeDependency(final Long... dependencyIds) {
    withSqlWritable { Sql sql ->
      sql.executeUpdate("""DELETE FROM jdbs_deps WHERE id IN (${dependencyIds.join ', '})""" as String)
    }
  }

  void validateJarsInDropins(List<File> jars, String driverName) {
    if (!jars) {
      return
    }
    def dropinsDirs = configService != null ? configService.getDropinsDirs() : null
    if (!dropinsDirs) {
      throw new IllegalStateException('Dropins directories not configured')
    }
    jars.eachWithIndex { jar, i ->
      if (!jar.isAbsolute() && !jar.exists()) {
        def homeDir = configService != null ? configService.getHomeDir() : null
        def candidates = []
        dropinsDirs.each { dropinsDir ->
          candidates << new File(dropinsDir, jar.path)
        }
        if (homeDir) {
          candidates << new File(homeDir, jar.path)
        }
        def resolved = candidates.find { it.exists() }
        if (resolved) {
          jars[i] = resolved
        }
      }
      if (!jars[i].exists()) {
        throw new InvalidJarLocationException(ErrorCode.INVALID_JAR_LOCATION, jars[i])
      }
      def resolvedJar = jars[i]
      def jarCanonical = resolvedJar.canonicalPath
      boolean inDropins = dropinsDirs.any { dropinsDir ->
        dropinsDir.exists() && jarCanonical.startsWith(dropinsDir.canonicalPath)
      }
      if (!inDropins) {
        throw new InvalidJarLocationException(ErrorCode.INVALID_JAR_LOCATION, resolvedJar)
      }
      boolean inDriverDir = dropinsDirs.any { dropinsDir ->
        def driverDir = new File(dropinsDir, driverName)
        driverDir.exists() && jarCanonical.startsWith(driverDir.canonicalPath)
      }
      if (!inDriverDir) {
        throw new InvalidJarLocationException(ErrorCode.INVALID_JAR_DRIVER_DIR, resolvedJar)
      }
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

  protected JdbsDriver rowToBean(final GroovyRowResult row) {
    row == null ?
        null :
        new JdbsDriver(
            id: row.id as Long,
            name: row.name as String,
            driverClass: row.clazz as String,
            driverClassExpr: row.clazz_expr as String
        )
  }
}
