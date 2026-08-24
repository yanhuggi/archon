package com.archon.jdbsee.jdbcli.config

import groovy.transform.EqualsAndHashCode;

/**
 * Domain model for db alias settings.
 */
@EqualsAndHashCode
class JdbsAlias {
    Long id
    String name
    String url
    String username
    String password
    String remark
    Long driverId
    JdbsDriver driver
}
