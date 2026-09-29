@echo off
chcp 65001 >nul
title Sea Louder - local downloader
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

echo Starting Sea Louder server...
"%PY%" server.py %*
echo.
echo Server stopped. Press any key to close.
pause >nul