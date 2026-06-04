@echo off
REM ============================================================
REM  Outlaw CodeMaker - launcher
REM  Double-click this file (or run it) to start the app.
REM  On first run it builds the virtual environment and installs
REM  dependencies automatically. After that it just launches.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PYEXE=.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo [Outlaw CodeMaker] First run - creating virtual environment...
    py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv
    if not exist "%PYEXE%" (
        echo [ERROR] Could not create the virtual environment.
        echo         Make sure Python 3.12+ is installed and on PATH.
        pause
        exit /b 1
    )
    echo [Outlaw CodeMaker] Installing dependencies (one time, a few minutes)...
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. See messages above.
        pause
        exit /b 1
    )
)

echo [Outlaw CodeMaker] Launching (via boot_restore failsafe)...
"%PYEXE%" boot_restore.py
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [Outlaw CodeMaker] Exited with code %RC%.
    echo If this was unexpected, scroll up for the traceback.
    pause
)
endlocal
