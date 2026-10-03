@echo off
cd /d "%~dp0"
title Video Dubbing V3 - keep this window open (close it to stop the app)
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Installing the new interface components...
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)
".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
