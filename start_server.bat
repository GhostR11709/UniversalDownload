@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\pythonw.exe" (
  start "" /b "%ROOT%.venv\Scripts\pythonw.exe" -m server
) else (
  start "" /b py -3 -m server
)
echo UniversalDownload is starting in the background.
echo Dashboard: http://127.0.0.1:8756/
endlocal
