@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    py -3.12 main.py %*
)
set appExitCode=%errorlevel%
if not "%appExitCode%"=="0" (
    echo.
    echo Application startup failed. Run start_client.cmd --check to see the reason.
    pause
)
exit /b %appExitCode%
