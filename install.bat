@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python bootstrap.py
if errorlevel 1 exit /b 1
echo.
echo agentjob installed. Run run_agent.bat next.
