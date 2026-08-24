@echo off
setlocal
set "APP_HOME=%~dp0.."
if defined JAVA_HOME if exist "%JAVA_HOME%\bin\java.exe" set "JAVA_EXE=%JAVA_HOME%\bin\java.exe"
if not defined JAVA_EXE set "JAVA_EXE=java"
"%JAVA_EXE%" %DEFAULT_JVM_OPTS% %JAVA_OPTS% %JDBSEE_OPTS% "-Djdbsee.home=%APP_HOME%" "-Dtextdb.allow_full_path=true" "-Dfile.encoding=UTF-8" -cp "%APP_HOME%\lib\*" com.archon.jdbsee.jdbcli.CliApplication %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
