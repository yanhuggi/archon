package com.archon.jdbsee.jdbcli;

import org.jdbi.v3.core.statement.Query;

/**
 * Callback that accepts a Query to be eecuted only within the scope the callback..
 */
@FunctionalInterface
public interface QueryCallback<T, X extends Throwable>
{
  /**
   * Will be invoked with a query to be executed.
   *
   * @param query query to be used only within scope of this callback
   * @return The return value of the callback
   * @throws Throwable optional exception thrown by the callback
   */
  T doWithQuery(Query query) throws Throwable;
}
