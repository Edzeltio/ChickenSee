@echo off
:: ════════════════════════════════════════════════════════════════════════════
:: start.bat  —  Windows launcher for the Python sensor logger.
::
:: [CONFIG] If Python is not on PATH, set the full path here:
::   set PYTHON="C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
:: ════════════════════════════════════════════════════════════════════════════

echo === Sensor Logger (Python) ===
echo.

:: ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found.
        echo         Install it from https://www.python.org
        echo         Make sure to check "Add Python to PATH" during install.
        pause
        exit /b 1
    )
    set PYTHON=py
) else (
    set PYTHON=python
)

:: ── Install dependencies if missing ───────────────────────────────────────
%PYTHON% -c "import serial" >nul 2>&1
if errorlevel 1 (
    echo [setup] Installing Python dependencies...
    %PYTHON% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        echo         Try running this manually:
        echo           pip install -r sensor\requirements.txt
        pause
        exit /b 1
    )
    echo.
)

:: ── Copy .env if missing ──────────────────────────────────────────────────
if not exist "%~dp0.env" (
    copy "%~dp0.env.example" "%~dp0.env" >nul
    echo [setup] Created sensor\.env
    echo         Open it and fill in your SUPABASE_URL and SUPABASE_KEY.
    echo.
    notepad "%~dp0.env"
    pause
)

:: ── Start ─────────────────────────────────────────────────────────────────
echo Starting...
echo.
cd /d "%~dp0"
%PYTHON% python\main.py

pause
