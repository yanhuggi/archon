package com.archon.jdbsee.jdbcli;

@FunctionalInterface
public interface CheckedFunction<T, R, E extends Exception> {
  R apply (T t) throws E;
}
