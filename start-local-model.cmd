@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.12 from python.org first.
    pause
    exit /b 1
)
if "%~1"=="" (
    py -3.12 scripts\start_local.py --ollama
) else (
    py -3.12 scripts\start_local.py --ollama --model "%~1"
)
if errorlevel 1 (
    echo.
    echo Local model startup failed. Copy the error above for troubleshooting.
    pause
    exit /b 1
)
