@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'UD Server.exe' -or $_.CommandLine -match 'UniversalDownload.*-m server|server.*UniversalDownload' }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo UniversalDownload server stopped.
endlocal
