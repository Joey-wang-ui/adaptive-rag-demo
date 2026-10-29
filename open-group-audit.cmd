@echo off
cd /d "%~dp0"
py -3.12 -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo Python 3.12 with Tkinter is required. Install the official Python Windows package.
  pause
  exit /b 1
)
py -3.12 "%~dp0wechat-audit\group_launch.py" desktop
if errorlevel 1 pause
