@echo off
setlocal
cd /d "%~dp0"
echo Updating UniversalDownload from GitHub...
call "%~dp0stop_server.bat" >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
if errorlevel 1 (
  echo Update failed.
  pause
  exit /b 1
)
echo Update complete. Restarting the background server...
call "%~dp0start_server.bat"
pause
endlocal
