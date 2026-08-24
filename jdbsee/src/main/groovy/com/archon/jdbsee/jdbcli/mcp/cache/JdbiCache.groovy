package com.archon.jdbsee.jdbcli.mcp.cache

import com.archon.jdbsee.jdbcli.DriverManagerDataSource
import com.archon.jdbsee.jdbcli.DriverManagerFacade
import com.archon.jdbsee.jdbcli.MiscTools
import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import org.jdbi.v3.core.Jdbi
import org.slf4j.Logger
import org.slf4j.LoggerFactory

/**
 * Caches {@link Jdbi} instances keyed by alias so that a single
 * data source (and its connection pool) is reused per alias instead of
 * being re-created on every tool call.
 */
class JdbiCache {
  private static final Logger LOG = LoggerFactory.getLogger(JdbiCache)

  private Map<String, Jdbi> cache = [:]
  private JdbsAliasDao aliasDao
  private DriverManagerFacade driverManagerFacade

  JdbiCache(JdbsAliasDao aliasDao, DriverManagerFacade driverManagerFacade) {
    this.aliasDao = aliasDao
    this.driverManagerFacade = driverManagerFacade
  }

  Jdbi getJdbi(String alias) {
    if (!alias) {
      throw new IllegalArgumentException("Alias name cannot be null")
    }
    Jdbi jdbi = cache[alias]
    if (jdbi) {
      LOG.debug("Jdbi cache hit for alias: {}", alias)
      return jdbi
    }
    LOG.info("Creating Jdbi instance for alias: {} (lazy load)", alias)
    jdbi = createJdbiForAlias(alias)
    cache[alias] = jdbi
    return jdbi
  }

  private Jdbi createJdbiForAlias(String alias) {
    def aliasDetails = aliasDao.findAlias(alias).orElse(null)
    if (!aliasDetails) {
      throw new IllegalArgumentException("Database alias not found: ${alias}. Please create the alias first using: jdbsee alias create")
    }
    def dataSource = new DriverManagerDataSource(
        url: aliasDetails.url,
        username: aliasDetails.username ?: "",
        password: aliasDetails.password ?: "",
        driverClassName: aliasDetails.driver.driverClass,
        driverManagerFacade: driverManagerFacade
    )
    loadDriver(dataSource, aliasDetails)
    return Jdbi.create(dataSource)
  }

  private void loadDriver(DriverManagerDataSource dataSource, def aliasDetails) {
    String driverClass = dataSource.driverClassName
    if (!driverClass) {
      return
    }
    def jarPaths = (aliasDetails.driverDetails?.jars?.collect { it.file?.absolutePath } ?: []).findAll { it }
    try {
      def driver = MiscTools.loadDriver(driverClass, jarPaths as String[])
      if (driver) {
        dataSource.driver = driver
        LOG.debug('Loaded driver {} for alias', driverClass)
      } else {
        LOG.warn('Driver {} not found in jars {}', driverClass, jarPaths)
      }
    } catch (Exception e) {
      LOG.warn('Failed to load driver {}: {}', driverClass, e.message)
    }
  }

  Set<String> getCachedAliases() {
    cache.keySet() as Set
  }

  void close() {
    LOG.info("Closing {} Jdbi instance(s)", cache.size())
    cache.each { alias, jdbi ->
      try {
        jdbi.close()
        LOG.debug("Closed Jdbi for alias: {}", alias)
      } catch (Exception e) {
        LOG.warn("Failed to close Jdbi for alias: {}", alias, e)
      }
    }
    cache.clear()
  }
}
