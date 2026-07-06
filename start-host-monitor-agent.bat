@echo off
setlocal
cd /d "%~dp0"
set HOST_MONITOR_AGENT_HOST=0.0.0.0
set HOST_MONITOR_AGENT_PORT=8765
python -u tools\host_monitor_agent.py
