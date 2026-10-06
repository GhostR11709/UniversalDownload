@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
if exist "%ROOT%UD Server.exe" (
  start "" /b "%ROOT%UD Server.exe"
) else if exist "%ROOT%.venv\Scripts\pythonw.exe" (
  start "" /b "%ROOT%.venv\Scripts\pythonw.exe" -m server
) else (
  start "" /b py -3 -m server
)
echo UniversalDownload is starting in the background.
endlocal
