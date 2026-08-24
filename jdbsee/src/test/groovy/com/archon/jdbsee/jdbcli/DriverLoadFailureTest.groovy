package com.archon.jdbsee.jdbcli

import org.junit.Test

import javax.sql.DataSource
import java.sql.Driver

import static org.junit.Assert.*

/**
 * Driver probing must stay tolerant per candidate but must not swallow the
 * final reason. Reporting "DataSource is null" for a refused connection sent
 * users after a driver-configuration problem they did not have.
 */
class DriverLoadFailureTest {

  /** Exposes the protected probing hook with a controllable testDataSource. */
  private static class ProbeService extends AbstractDbCommandService {
    ProbeService() { super() }
  }

  @Test
  void rethrowsTheLastRealFailureWhenNoCandidateWorks() {
    def service = new ProbeService()
    def jar = jarWithDriverNamedClass()

    // Mimics withDynamicDataSource: the connection test fails for every
    // candidate, which is the case that used to be reported as a null
    // DataSource.
    Closure<DataSource> failing = { Driver driver ->
      throw new IllegalStateException('Cannot initialize a dataSource',
          new java.net.ConnectException('Connection refused'))
    }

    try {
      service.registerDriverClass(null, '(.*)Driver(.*)', jar,
          this.class.classLoader, failing)
      // No candidate in this jar is a loadable driver, so loadDriver fails
      // first; either way the method must not return null silently after a
      // failure was seen.
      fail('should have rethrown the probing failure')
    } catch (IllegalStateException e) {
      assertNotNull("the cause must be preserved for classification", e.cause)
    }
  }

  @Test
  void returnsNullWhenThereWasNothingToProbe() {
    def service = new ProbeService()
    def jar = jarWithoutAnyMatchingClass()

    // A jar with no class matching the driver pattern is not a failure: the
    // caller moves on to the next jar.
    def result = service.registerDriverClass(null, '(.*)Driver(.*)', jar,
        this.class.classLoader, { Driver d -> null })

    assertNull(result)
  }

  @Test
  void rootMessageReachesTheInnermostCause() {
    def method = AbstractDbCommandService.getDeclaredMethod('rootMessage', Throwable)
    method.accessible = true

    def wrapped = new RuntimeException('outer',
        new IllegalStateException('middle',
            new java.net.ConnectException('Connection refused')))

    assertEquals('Connection refused', method.invoke(null, wrapped))
  }

  @Test
  void rootMessageFallsBackToTheTypeWhenThereIsNoMessage() {
    def method = AbstractDbCommandService.getDeclaredMethod('rootMessage', Throwable)
    method.accessible = true

    assertEquals('NullPointerException', method.invoke(null, new NullPointerException()))
  }

  /** A jar holding one class whose name matches the driver pattern. */
  private static File jarWithDriverNamedClass() {
    return buildJar('com/example/FakeDriver.class')
  }

  private static File jarWithoutAnyMatchingClass() {
    return buildJar('com/example/Helper.class')
  }

  private static File buildJar(String entryName) {
    File jar = File.createTempFile('probe', '.jar')
    jar.deleteOnExit()
    def out = new java.util.jar.JarOutputStream(new FileOutputStream(jar))
    try {
      out.putNextEntry(new java.util.zip.ZipEntry(entryName))
      // Not a real class file; loadDriver is expected to fail on it.
      out.write('not bytecode'.bytes)
      out.closeEntry()
    } finally {
      out.close()
    }
    return jar
  }
}
