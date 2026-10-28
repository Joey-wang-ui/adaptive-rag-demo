@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.12 from python.org first.
    pause
    exit /b 1
)
echo Downloading the official Qwen2.5 3B model with SHA-256 verification.
echo No changes to system DNS, hosts, proxy settings or HTTPS verification.
echo Keep your working Internet connection or system proxy enabled.
py -3.12 scripts\download_ollama_model.py
if errorlevel 1 (
    echo.
    echo Model download did not finish. Keep this window for troubleshooting.
    pause
    exit /b 1
)
call start-local-model.cmd
