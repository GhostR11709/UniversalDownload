@echo off
setlocal EnableExtensions
title GhostR Universal Downloader - setup
set "ROOT=%~dp0"
cd /d "%ROOT%"
echo GhostR Universal Downloader setup
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Install it from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  pause
  exit /b 1
)
if not exist "%ROOT%.venv\Scripts\python.exe" py -3 -m venv "%ROOT%.venv"
if errorlevel 1 goto :failed
"%ROOT%.venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
"%ROOT%.venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :failed
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\build_server.ps1"
if errorlevel 1 goto :failed
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\install_ffmpeg.ps1"
if errorlevel 1 goto :failed
"%ROOT%.venv\Scripts\python.exe" -m server --install-autostart >nul
call "%ROOT%start_server.bat"
echo.
echo Setup complete. Load the extension from:
echo %ROOT%extension
echo Open chrome://extensions, enable Developer mode, then click Load unpacked.
echo The server is running quietly in the background; the dashboard will not open automatically.
start "" "chrome://extensions/"
pause
exit /b 0
:failed
echo Setup failed. Fix the message above and run setup.bat again.
pause
exit /b 1
