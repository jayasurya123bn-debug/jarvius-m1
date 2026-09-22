@echo off
title JARVIS Terminator
echo Closing all running JARVIS background processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*jarvis*' -and ($_.Name -like '*python*' -or $_.Name -like '*pythonw*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /F /FI "WINDOWTITLE eq JARVIS*" 2>nul
echo Done! All JARVIS processes are closed.
timeout /t 2 >nul
