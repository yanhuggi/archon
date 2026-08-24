package com.archon.jdbsee.jdbcli

import com.archon.jdbsee.jdbcli.config.JdbsAliasDao
import com.archon.jdbsee.jdbcli.config.JdbsDep
import com.archon.jdbsee.jdbcli.config.JdbsJar
import org.jboss.shrinkwrap.resolver.api.maven.Maven
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.inject.Inject
import javax.sql.DataSource
import java.sql.Driver
import java.util.jar.JarEntry
import java.util.jar.JarFile

/**
 * Resolves db-access command options providing a connection to the relevant db.
 */
//@CompileStatic
abstract class AbstractDbCommandService {

  private final static Logger LOG = LoggerFactory.getLogger (AbstractDbCommandService.class)
  private static final int DEFAULT_WIDTH = 80;

  @Inject
  public ConfigService configService
  @Inject
  public DriverManagerFacade driverManagerFacade
  @Inject
  public JdbsAliasDao jdbsAliasDao

  public AbstractDbCommandService(){}

  def doRun(final AbstractDbCommand dbCommand, Closure<Void> dbCallback) {
    dbCommand.with {
      String actualpass = askForPassword ? System.console().readPassword() : password

      def aliasDetails = alias != null ? jdbsAliasDao.findAlias(alias).orElse(null) : null

      def _username = { username ?: aliasDetails?.username ?: '' }
      def _password = { actualpass ?: aliasDetails?.password ?: '' } as Closure<String>
      def _driverClassName = { driverClassName ?: aliasDetails?.driverDetails?.driverClass }
      def _url = { url ?: aliasDetails?.url }
      def _driverClassMatches = { driverClassMatches ?: aliasDetails?.driverDetails?.driverClassExpr }

      final Closure<DataSource> createDataSource = { driver ->

        new DriverManagerDataSource(
            username: _username(),
            password: _password(),
            driverClassName: _driverClassName(),
            url: _url(),
            driverManagerFacade: driverManagerFacade,
            driver: driver
        )
      } as Closure<DataSource>

      def alljars = []
      if (jars) {
        alljars.addAll(jars)
      }
      def alldeps = []
      if (deps) {
        alldeps.addAll(deps)
      }

      if (aliasDetails) {
        //consider alias deps and jars for resolution
        aliasDetails.driverDetails?.jars?.each { JdbsJar jar->
          alljars << jar.file
        }
        aliasDetails.driverDetails?.deps?.each { JdbsDep dep->
          alldeps << dep.gav
        }
      }

      configService.getDropinsDirs().each {File dropinsDir->
        LOG.debug('Looking for dropins at {}', dropinsDir)
        if (dropinsDir && dropinsDir.exists()) {
          alljars.addAll(dropinsDir.listFiles({ dir, name -> name.toLowerCase().endsWith('.jar') } as FilenameFilter))
        }
      }
      alljars.flatten()
      if (alljars || alldeps) {
        LOG.debug('Loading jars {} and deps {}', alljars, alldeps)
        withDynamicDataSource(_driverClassName(), _driverClassMatches(), alljars, alldeps, createDataSource, dbCallback)
      } else {//no additional jars or deps... assume the driver manager is able to find the driver
        dbCallback(new DriverManagerDataSource(
            url: _url(),
            username: _username(),
            password: _password(),
            driverManagerFacade: driverManagerFacade
        ))
      }
    }
  }

  /**
   * Creates and test a new datasource opening  a connection
   */
  protected void withDynamicDataSource(
      final String driverClassName,
      final String driverClassMatches,
      final List<File> jars,
      final List<String> deps,
      final Closure<DataSource> createDataSource,
      final Closure<Void> callback) {
    Closure<DataSource> testDataSource = {Driver driver->
      def tmpDataSource = createDataSource(driver)
      try {
        tmpDataSource.getConnection().close()//tries to acquire a connection and release it
        return tmpDataSource
      } catch (Exception e) {
        throw new IllegalStateException ("Cannot initialize a dataSource for driverClassName: $driverClassName, driverClassMatches: $driverClassMatches, jars: $jars, deps: $deps", e)
      }
    }

    withDeps(driverClassName, driverClassMatches, jars, deps, testDataSource) {DataSource dataSource ->
        callback(dataSource)
    }
  }

  /**
   * Run callback on the datasource  obtained from specified params.
   *
   * @param driverClassName the driver class (optional)
   * @param driverClassMatches an expression for driver class name matching (optional)
   * @param jars jars to search for driver loading
   * @param deps deps to solve for driver loading
   * @param testDataSource tests connections from datasource
   * @param callback called passing the datasource
   */
  protected void withDeps(
          final String driverClassName,
          final String driverClassMatches,
          final List<File> jars,
          final List<String> deps,
          final Closure<DataSource> testDataSource,
          final Closure callback) {
    final ClassLoader currThreadClassLoader = Thread.currentThread().getContextClassLoader();
    try {
      //chain jars classloader with original one
      final URL[] jarUrls = jars
              .findAll { it.exists() }         // existing file
              .collect { it.toURI().toURL() }.toArray(new URL[0])
      final ClassLoader cl
      if (jars) {
        cl = new URLClassLoader(jarUrls, currThreadClassLoader)
      } else {
        cl = currThreadClassLoader
      }
      GroovyClassLoader groovyClassLoader = new groovy.lang.GroovyClassLoader(cl)

      //resolve deps
      if (deps) {
        Maven.resolver().resolve(deps).withTransitivity().asFile().each {File file->
          groovyClassLoader.addURL file.toURI().toURL()
        }
      }

      LOG.info('Deps resolved {}', groovyClassLoader.getURLs())

      //collect dependency urls
      //since deps are cached, a diff would remove even the relevant ones
//      def urls = (ClasspathHelper.forClassLoader(groovyClassLoader) - ClasspathHelper.forClassLoader(currThreadClassLoader)).toArray() as URL[]
      def urls = ClasspathHelper.forClassLoader(groovyClassLoader)

      //add the URLs to the SystemClassLoader
      LOG.debug ('Determined deps urls {}', urls)
      // Scanning candidate classes is expected to fail for most of them, so a
      // failure here is not fatal on its own. But the last real reason is kept:
      // if no candidate yields a DataSource, reporting "DataSource is null"
      // instead of the actual cause sends the user after the wrong problem.
      Throwable lastFailure = null
      DataSource dataSource = urls.findResult { URL url ->
        DataSource result
        if (url.file.endsWith('jar')) {
          LOG.debug('Registering {}', url)
          def sysloader = ClassLoader.getSystemClassLoader()
            try {
              result = registerDriverClass(driverClassName, driverClassMatches, new File(url.file), new URLClassLoader([url] as URL[], sysloader), testDataSource);
            } catch (Throwable e) {
              LOG.debug('Driver registration failed for {}: {}', url, e.message)
              lastFailure = e
            }
        }
        return result
      } as DataSource

      if (dataSource == null && lastFailure != null) {
        throw new IllegalStateException(
            "Could not obtain a connection using the configured driver: ${rootMessage(lastFailure)}",
            lastFailure)
      }

      Thread.currentThread().setContextClassLoader(groovyClassLoader)
      callback(dataSource)
    } finally {//restore original classloader
      Thread.currentThread().setContextClassLoader(currThreadClassLoader)
    }
  }

  protected DataSource registerDriverClass (
          final String driverClassName,
          final String driverClassMatches,
          final File jar,
          final ClassLoader driversLoader,
          final Closure<DataSource> testDataSource) {
    final JarFile jarFile = new JarFile(jar)
    try {
      return probeJarForDriver(jarFile, driverClassName, driverClassMatches, jar, testDataSource)
    } finally {
      try {
        jarFile.close()
      } catch (IOException ignored) {
      }
    }
  }

  private DataSource probeJarForDriver(
          final JarFile jarFile,
          final String driverClassName,
          final String driverClassMatches,
          final File jar,
          final Closure<DataSource> testDataSource) {
    // Class loading failures are expected while probing, but a connection
    // failure from testDataSource is the answer the user needs. Keep the last
    // one and rethrow it if no candidate in this jar works out.
    Throwable lastFailure = null
    for (JarEntry jarEntry : Collections.list(jarFile.entries())) {
      final String name = jarEntry.getName();
      if (name.endsWith(".class")) {
        String classFQN = name.replace("/", ".").replaceAll('\\.class$', '')
        if ((driverClassName && classFQN == driverClassName) ||
                (!driverClassName && driverClassMatches && classFQN.matches(driverClassMatches))) {
          try {
            LOG.trace('Analyzing jar class {}', name)
            Driver driver = MiscTools.loadDriver(classFQN, jar.absolutePath)
            if (driver) {
              def sysprop = System.getProperty("jdbc.drivers")
              System.setProperty("jdbc.drivers", "${sysprop}:${classFQN}")

              return testDataSource(driver)
            }
          } catch (Throwable ignore) {
            // Safe to ignore per candidate: this is a hack to force loading
            // naive drivers, and most matched classes are not usable drivers.
            // Throwable rather than Exception because a corrupt or foreign
            // class file raises ClassFormatError / UnsupportedClassVersionError,
            // which would otherwise abort the whole command.
            LOG.debug('Cannot use {} as a driver: {}', classFQN, ignore.message)
            lastFailure = ignore
          }
        }
      }
    }
    if (lastFailure != null) {
      throw new IllegalStateException(rootMessage(lastFailure), lastFailure)
    }
    return null
  }

  /** Innermost message in the cause chain, which names the real problem. */
  private static String rootMessage(Throwable t) {
    Throwable current = t
    while (current.cause != null && current.cause != current) {
      current = current.cause
    }
    return current.message ?: current.class.simpleName
  }

  int getWidth(final AbstractDbCommand abstractDbCommand) {
    def width = abstractDbCommand.width
    return width>0?width:computeWidth()
  }

  protected int computeWidth () {
    return DEFAULT_WIDTH
  }


}
