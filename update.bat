@echo off
cd /d "%~dp0"
title Video Dubbing V3 - Update
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
echo Updating the YouTube downloader and other packages...
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
echo Done.
pause
