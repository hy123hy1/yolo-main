@echo off
setlocal
cd /d "%~dp0"
schtasks /Create /TN "YOLO Host Monitor Agent" /SC ONLOGON /TR "\"%~dp0start-host-monitor-agent.bat\"" /F
if errorlevel 1 (
  echo Failed to install scheduled task. Please run this file as Administrator.
  exit /b 1
)
schtasks /Run /TN "YOLO Host Monitor Agent"
echo Host monitor scheduled task installed and started.
