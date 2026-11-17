@echo off
setlocal
cd /d "%~dp0"
python -c "import json, pathlib, sys; sys.path.insert(0, 'tools'); import host_monitor_agent as h; pathlib.Path('backend/data').mkdir(parents=True, exist_ok=True); pathlib.Path('backend/data/host_monitor_resources.json').write_text(json.dumps(h.make_response(), ensure_ascii=False), encoding='utf-8'); print('host monitor snapshot written')"
