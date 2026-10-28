@echo off
setlocal
cd /d "%~dp0"
title Adaptive RAG - DeepSeek API
where py >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.12, then reopen this file.
    pause
    exit /b 1
)
echo Starting with the official DeepSeek API.
echo No Ollama service or local model download is needed.
echo Your API key is entered privately in this terminal and is not saved.
set "PYTHONIOENCODING=utf-8"
py -3.12 -m scripts.start_api
if errorlevel 1 (
    echo.
    echo API startup did not finish. See the error above.
    pause
    exit /b 1
)
