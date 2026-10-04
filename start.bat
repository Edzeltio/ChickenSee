@echo off
setlocal

title Poultry Monitoring System Launcher

:: Resolve all paths from this file, even when launched from another directory.
cd /d "%~dp0"
if errorlevel 1 goto :failed
set "VENV_DIR=%~dp0venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%~dp0requirements.txt"
set "REQ_STAMP=%VENV_DIR%\requirements-installed.txt"

echo ===================================================
echo       Poultry Monitoring System Launcher
echo ===================================================
echo.

if not exist "%REQ_FILE%" (
    echo [ERROR] requirements.txt is missing. Restore it before starting.
    goto :failed
)

:: Use the Windows Python launcher when available, otherwise Python on PATH.
if exist "%VENV_PYTHON%" goto :dependencies
echo [INFO] First run: creating the virtual environment...
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv "%VENV_DIR%"
) else (
    python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Install Python 3.10 or newer and add it to PATH.
        goto :failed
    )
    python -m venv "%VENV_DIR%"
)
if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    goto :failed
)

:dependencies
:: Only stamp a successful install. Failed installs are retried on the next run.
:: Reinstall when requirements change, including for an older existing venv.
if not exist "%REQ_STAMP%" goto :install
fc /b "%REQ_FILE%" "%REQ_STAMP%" >nul 2>&1
if not errorlevel 1 goto :run

:install
echo [INFO] Installing dependencies into the virtual environment...
if exist "%REQ_STAMP%" del "%REQ_STAMP%"
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%VENV_PYTHON%" -m pip install -r "%REQ_FILE%"
if errorlevel 1 goto :failed
copy /y "%REQ_FILE%" "%REQ_STAMP%" >nul
if errorlevel 1 goto :failed
echo [SUCCESS] Environment setup complete.

:run
:: main.py owns the collector, camera, and recurring Supabase sync thread.
echo [INFO] Starting monitoring and continuous Supabase sync...
"%VENV_PYTHON%" -u "%~dp0python\main.py" %*
if errorlevel 1 goto :failed
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup or monitoring failed. See the error above.
echo Dependencies will be retried next time if setup did not finish.
pause
exit /b 1