@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
py -3.12 wechat-audit\bootstrap.py
if errorlevel 1 (
  echo.
  echo WeChat shipment bot setup or startup did not finish.
  echo Keep this window for troubleshooting. Never share API keys or QR login tokens.
  pause
)
endlocal
