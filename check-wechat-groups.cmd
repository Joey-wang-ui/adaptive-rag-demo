@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
py -3.12 wechat-audit\group_probe.py
echo.
pause
endlocal
