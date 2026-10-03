@echo off
setlocal
cd /d "%~dp0"
title Video Dubbing V3 - Installation
echo ==================================================
echo   Video Dubbing V3 - Installation
echo ==================================================
echo.

rem ---------------------------------------------------------------- Python
set "PYEXE="
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
for %%V in (313 312 311 310) do (
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if not defined PYEXE if exist "%ProgramFiles%\Python%%V\python.exe" set "PYEXE=%ProgramFiles%\Python%%V\python.exe"
)
if not defined PYEXE (
  echo [1/4] Installing Python 3.12...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
  echo.
  echo ERROR: Python could not be installed automatically.
  echo Install Python 3.12 from https://www.python.org/downloads/ then run install.bat again.
  pause
  exit /b 1
)
echo [1/4] Python OK: %PYEXE%

rem ---------------------------------------------------------------- FFmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" if not exist "%~dp0ffmpeg\bin\ffmpeg.exe" (
  echo [2/4] Installing FFmpeg...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)
echo [2/4] FFmpeg OK

rem ------------------------------------- Deno (needed by yt-dlp for YouTube)
where deno >nul 2>nul
if errorlevel 1 if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\deno.exe" if not exist "%USERPROFILE%\.deno\bin\deno.exe" (
  echo [3/4] Installing Deno - used by the YouTube downloader...
  winget install -e --id DenoLand.Deno --accept-package-agreements --accept-source-agreements
)
echo [3/4] Deno OK

rem ------------------------------------------------- Python packages (venv)
echo [4/4] Installing Python packages...
if not exist ".venv\Scripts\python.exe" "%PYEXE%" -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: could not create the Python environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo ERROR: package installation failed. Check your internet connection and try again.
  pause
  exit /b 1
)

rem ---------------------------------------------------------------- API keys
echo.
echo OpenAI API keys are entered in the app: open it with run.bat, then click Settings.
echo You can use one key for everything, or a separate key for Analysis, Script and Voice.
echo Create keys at https://platform.openai.com/api-keys

echo.
echo ==================================================
echo   Installation complete. Double-click run.bat
echo ==================================================
pause
