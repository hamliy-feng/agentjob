@echo off
chcp 65001 >nul
cd /d "%~dp0"

start "Job Agent Dashboard Guard" /min cmd /c python tools\dashboard_watchdog.py

for /l %%I in (1,1,15) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8799/api/health -TimeoutSec 1; if($r.StatusCode -eq 200){ exit 0 } } catch {}; exit 1" >nul 2>&1
  if not errorlevel 1 goto READY
  powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)

echo [Job Agent] Dashboard 8799 启动超时，请查看 data\dashboard_service.log
exit /b 1

:READY
echo [Job Agent] Dashboard 已在线：http://127.0.0.1:8799
start "" http://127.0.0.1:8799
exit /b 0
