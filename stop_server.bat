@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'UniversalDownload.*-m server|server.*UniversalDownload' }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo UniversalDownload server stopped.
endlocal
