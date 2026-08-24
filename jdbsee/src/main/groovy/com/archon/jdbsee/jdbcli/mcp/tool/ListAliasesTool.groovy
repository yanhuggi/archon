package com.archon.jdbsee.jdbcli.mcp.tool

import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject

/**
 * The "list_aliases" tool: lists the configured database aliases with
 * their URLs masked so that credentials never leak into the response.
 */
class ListAliasesTool {
  private static final Logger LOG = LoggerFactory.getLogger(ListAliasesTool)

  @Inject
  JdbsAliasDao aliasDao

  @Inject
  String defaultAlias

  @Inject
  ListAliasesTool() {}

  void setAliasDao(JdbsAliasDao aliasDao) { this.aliasDao = aliasDao }
  void setDefaultAlias(String defaultAlias) { this.defaultAlias = defaultAlias }

  Map<String, Object> execute(Map<String, Object> arguments) {
    try {
      def aliases = aliasDao.listAliases()
      def aliasList = aliases.collect { alias ->
        return [
            name  : alias.name,
            url   : maskSensitiveUrl(alias.url),
            driver: alias.driver?.name ?: "unknown"
        ]
      }
      return [success: true, aliases: aliasList, default_alias: defaultAlias, count: aliasList.size()]
    } catch (Exception e) {
      LOG.error("Failed to list aliases", e)
      return [success: false, error: [code: "LIST_ALIASES_FAILED", message: e.message]]
    }
  }

  /**
   * Masks credentials in a JDBC URL: both the userinfo form
   * (scheme://user:pass@host) and password-bearing query parameters
   * (?password=..., &pwd=...) used by several drivers.
   */
  private String maskSensitiveUrl(String url) {
    if (!url) {
      return url
    }
    String masked = url.replaceAll("://[^:/@]+:[^@/]+@", "://***:***@")
    masked = masked.replaceAll("(?i)([?&;](?:password|pwd|passwd)=)[^&;]*", '$1***')
    return masked
  }
}
