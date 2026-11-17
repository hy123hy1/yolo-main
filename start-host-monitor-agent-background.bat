@echo off
setlocal
cd /d "%~dp0"
if not exist output\host-monitor mkdir output\host-monitor
start "YOLO Host Monitor Agent" /min cmd /c "set HOST_MONITOR_AGENT_HOST=0.0.0.0&& set HOST_MONITOR_AGENT_PORT=8765&& set HOST_MONITOR_METRICS_FILE=%cd%\backend\data\host_monitor_resources.json&& set HOST_MONITOR_METRICS_INTERVAL_SECONDS=2&& python -u tools\host_monitor_agent.py >> output\host-monitor\agent.out.log 2>> output\host-monitor\agent.err.log"
echo Host monitor agent started.
echo Metrics file: %cd%\backend\data\host_monitor_resources.json
