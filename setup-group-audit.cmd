@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
py -3.12 wechat-audit\group_launch.py check
pause
