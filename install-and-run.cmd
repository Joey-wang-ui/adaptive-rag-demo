@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Adaptive RAG - Automatic Setup and Launch
echo Adaptive RAG: automatic local setup with Qwen2.5 3B.
echo Existing software and saved model downloads will be reused.
echo Missing software is installed through Microsoft WinGet.
echo Windows may ask you to approve an installer.
echo.

call :python_ready
if errorlevel 1 (
    call :install_package Python.Python.3.12
    if errorlevel 1 goto failed
    call :refresh_path
)
call :python_ready
if errorlevel 1 (
    echo Python 3.12 is unavailable. Finish its installation and reopen this file.
    goto failed
)

call :node_ready
if errorlevel 1 (
    call :install_package OpenJS.NodeJS.LTS
    if errorlevel 1 goto failed
    call :refresh_path
)
call :node_ready
if errorlevel 1 (
    echo Node.js or npm is unavailable. Finish installation and reopen this file.
    goto failed
)

call :ollama_ready
if errorlevel 1 (
    call :install_package Ollama.Ollama
    if errorlevel 1 goto failed
    call :refresh_path
)
call :ollama_ready
if errorlevel 1 (
    echo Ollama is unavailable. Finish its installation and reopen this file.
    goto failed
)

set "PYTHONIOENCODING=utf-8"
py -3.12 -m scripts.auto_local
if errorlevel 1 goto failed
exit /b 0

:python_ready
py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>nul
exit /b %errorlevel%

:node_ready
where npm.cmd >nul 2>nul
if errorlevel 1 exit /b 1
node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit((a===20 && b>=19) || (a===22 && b>=12) || a>22 ? 0 : 1)" >nul 2>nul
exit /b %errorlevel%

:ollama_ready
where ollama >nul 2>nul
if not errorlevel 1 exit /b 0
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" exit /b 0
exit /b 1

:install_package
where winget >nul 2>nul
if errorlevel 1 (
    echo Microsoft WinGet is unavailable; automatic software installation cannot continue.
    echo Install or update App Installer from Microsoft Store, then reopen this file.
    exit /b 1
)
echo Installing missing prerequisite: %~1
winget install --id "%~1" --exact --source winget --silent --accept-package-agreements --accept-source-agreements
exit /b %errorlevel%

:refresh_path
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path"`) do set "PATH=%%P"
exit /b 0

:failed
echo.
echo Setup or launch did not finish. Saved model download bytes are kept.
echo Fix the error above, then reopen install-and-run.cmd.
echo Python setup and download status: logs\auto-install.log
echo Backend details, if started: logs\local-server.log
pause
exit /b 1
