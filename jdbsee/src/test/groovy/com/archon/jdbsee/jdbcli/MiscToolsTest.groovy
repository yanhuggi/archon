package com.archon.jdbsee.jdbcli

import org.junit.Test

import java.sql.Driver

import static org.junit.Assert.*

/**
 * Driver loading backs the -j and -d paths. It caches into static state, so
 * these tests assert on observable behaviour rather than on cache contents.
 */
class MiscToolsTest {

  @Test
  void loadsARealDriverAndCachesTheInstance() {
    Driver first = MiscTools.loadDriver('org.hsqldb.jdbc.JDBCDriver')
    Driver second = MiscTools.loadDriver('org.hsqldb.jdbc.JDBCDriver')

    assertNotNull(first)
    assertEquals('org.hsqldb.jdbc.JDBCDriver', first.class.name)
    assertSame("a repeated load must come from the cache", first, second)
  }

  @Test
  void reportsAnUnknownDriverAsClassNotFound() {
    try {
      MiscTools.loadDriver('no.such.Driver')
      fail('should have reported the missing driver')
    } catch (ClassNotFoundException e) {
      assertTrue(e.message.contains('no.such.Driver'))
    }
  }

  /**
   * Probing matches classes by name pattern, so a class called *Driver* that is
   * not a java.sql.Driver is a normal occurrence. It must surface as
   * ClassNotFoundException, the documented outcome, not as the internal
   * ClassCastException.
   */
  @Test
  void aClassThatIsNotADriverIsReportedAsClassNotFound() {
    try {
      MiscTools.loadDriver('java.lang.String')
      fail('should not accept a non-driver class')
    } catch (ClassNotFoundException e) {
      assertTrue(e.message.contains('java.lang.String'))
    } catch (ClassCastException e) {
      fail("internal ClassCastException leaked: ${e.message}")
    }
  }

  @Test
  void aClassWithoutANoArgConstructorIsReportedAsClassNotFound() {
    // java.lang.Integer has no accessible no-arg constructor.
    try {
      MiscTools.loadDriver('java.lang.Integer')
      fail('should not accept a class it cannot instantiate')
    } catch (ClassNotFoundException e) {
      assertTrue(e.message.contains('java.lang.Integer'))
    }
  }

  @Test
  void addToClasspathToleratesNullAndEmptyInput() {
    assertNotNull(MiscTools.addToClasspath())
    assertNotNull(MiscTools.addToClasspath((String[]) null))
    assertNotNull(MiscTools.addToClasspath('', '   '))
  }

  @Test
  void addToClasspathReturnsTheSameLoaderEachTime() {
    // The loader is process-wide: callers rely on additions accumulating.
    assertSame(MiscTools.addToClasspath(), MiscTools.addToClasspath())
  }

  @Test
  void addToClasspathAcceptsAJarPathAndMakesItLoadable() {
    File jar = File.createTempFile('empty', '.jar')
    jar.deleteOnExit()
    def out = new java.util.jar.JarOutputStream(new FileOutputStream(jar))
    out.close()

    def loader = MiscTools.addToClasspath(jar.absolutePath)

    assertNotNull(loader)
    assertTrue("the jar should now be on the loader's URL list",
        ((URLClassLoader) loader).URLs*.toString().any { it.contains(jar.name) })
  }
}
