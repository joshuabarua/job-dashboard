@echo off
title Job Search Command Center
cd /d C:\Users\Josh\job-dashboard

echo Stopping any existing server...
powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
timeout /t 1 /nobreak >nul

echo Starting Job Search Command Center...
echo The dashboard opens in your browser. Close this window or press Ctrl+C to stop.
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"
C:\Python312\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause