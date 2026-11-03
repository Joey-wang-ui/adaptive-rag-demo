@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install Python 3.12 from python.org.
    pause
    exit /b 1
)
py -3.12 scripts\start_jiahui.py %*
if errorlevel 1 (
    echo.
    echo Jiahui startup failed. Check logs\jiahui-server.log and the error above.
    pause
    exit /b 1
)
