@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install Python 3.12 from python.org.
    pause
    exit /b 1
)
py -3.12 scripts\start_local.py
if errorlevel 1 (
    echo.
    echo Startup failed. Copy the error above for troubleshooting.
    pause
    exit /b 1
)
