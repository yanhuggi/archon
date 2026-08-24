package com.archon.jdbsee.jdbcli;

import com.archon.jdbsee.jdbcli.exception.CommandFailedException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine;
import picocli.CommandLine.IFactory;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class CliApplication implements Runnable {

  private static final Logger LOG = LoggerFactory.getLogger(CliApplication.class);

  public void run() {
    CommandLine.usage(this, System.err);
  }

  public static void main(final String[] args) {
    multiCmd(new String[][] {args});
  }

  public static void multiCmd(final String[][] multiArgs) {
    Thread.currentThread().setContextClassLoader(MiscTools.addToClasspath());
    DaggerAppComponent.Builder builder = DaggerAppComponent.builder();
    final AppComponent appComponent = builder.build();
    try {
      appComponent.getAliasPasswordMigrationService().migrateIfNeeded();
    } catch (Exception e) {
      LOG.error("Alias password migration failed: {}", e.getMessage(), e);
    }

    final AsciiTableResultSetScanner ascii = new AsciiTableResultSetScanner();
    appComponent.inject(ascii);
    final ConsoleService consoleService = new ConsoleService(ascii);
    appComponent.inject(consoleService);

    final MainCommand mainCommand = appComponent.getMainCommand();
    mainCommand.setAppComponent(appComponent);
    mainCommand.setConsoleService(consoleService);

    final IFactory daggerFactory = new AppIFactory(appComponent);
    CommandLine commandLine = new CommandLine(mainCommand, daggerFactory);
    int exitCode = 0;
    for (final String[] args : multiArgs) {
      try {
        commandLine.parseWithHandler(new CommandLine.RunLast(), System.err, args);
      } catch (CommandLine.ExecutionException e) {
        exitCode = reportFailure(e);
      }
    }
    if (exitCode != 0) {
      System.exit(exitCode);
    }
  }

  /**
   * Reports a command failure without burying the structured output.
   *
   * When the command already emitted a formatted error (signalled by
   * CommandFailedException) only a one-line summary goes to stderr; anything
   * else still gets a stack trace because it was not reported anywhere else.
   */
  private static int reportFailure(final CommandLine.ExecutionException e) {
    Throwable cause = e.getCause();
    if (cause instanceof CommandFailedException) {
      Throwable root = ErrorMapper.rootCause(cause);
      System.err.println("Command failed: " + root.getMessage());
      LOG.debug("Command failed", cause);
      return 1;
    }
    e.printStackTrace(System.err);
    return 1;
  }
}

class AppIFactory implements IFactory {
  private final AppComponent appComponent;

  AppIFactory(final AppComponent appComponent) {
    this.appComponent = appComponent;
  }

  @Override
  public <T> T create(Class<T> cls) throws Exception {
    if (List.class.isAssignableFrom(cls)) {
      return (T) new ArrayList();
    }
    if (Set.class.isAssignableFrom(cls)) {
      return (T) new HashSet();
    }
    if (Map.class.isAssignableFrom(cls)) {
      return (T) new HashMap();
    }
    Constructor<T> constructor = cls.getDeclaredConstructor();
    constructor.setAccessible(true);
    T instance = constructor.newInstance();

    final Method method = appComponent.getClass().getMethod("inject", new Class[]{cls});
    if (method == null) {
      throw new IllegalArgumentException(String.format("Cannot create command instance for %s", cls));
    }
    //inject fields
    method.invoke(appComponent, instance);

    return instance;
  }
}
