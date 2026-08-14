@echo off
setlocal

rem WSL and Windows Python environments are not interchangeable. Keep the
rem Windows environment separate when both systems use one checkout.
set "ARCHON_JIRA_PROJECT=%~dp0.."
set "UV_PROJECT_ENVIRONMENT=.venv-windows"

rem pywin32's DLL directory is not discovered reliably from a WSL UNC path.
rem server.windows_launcher adds it to the Python process before MCP imports.
uv --directory "%ARCHON_JIRA_PROJECT%" run python -m server.windows_launcher %*
exit /b %ERRORLEVEL%
