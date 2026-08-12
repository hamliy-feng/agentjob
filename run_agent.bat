@echo off
chcp 65001 >nul
cd /d "%~dp0"

python tools\check_9227.py >nul 2>&1
if errorlevel 1 (
  echo [Job Agent] 9227 未就绪，正在启动专用浏览器...
  python tools\start_9227.py
  powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
) else (
  echo [Job Agent] 9227 已在线。
)

start "Job Agent Dashboard Guard" /min cmd /c python tools\dashboard_watchdog.py
start "Job Agent Browser Worker" cmd /k python browser_worker.py
start "Job Agent Executor" cmd /k python executor.py

for /l %%I in (1,1,15) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8799/api/health -TimeoutSec 1; if($r.StatusCode -eq 200){ exit 0 } } catch {}; exit 1" >nul 2>&1
  if not errorlevel 1 goto DASHBOARD_READY
  powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)
echo [Job Agent] Dashboard 8799 启动超时，请查看 data\dashboard_service.log
goto AFTER_DASHBOARD

:DASHBOARD_READY
start "" http://127.0.0.1:8799
:AFTER_DASHBOARD

echo.
echo Dashboard: http://127.0.0.1:8799
echo Browser CDP: http://127.0.0.1:9227
echo 个人资料与求职要求请在 Dashboard 顶栏维护；我的资料.txt 会自动生成兼容备份。
echo 投递必须在 Dashboard 由用户点击确认；L6 无人监督聊天默认关闭。
