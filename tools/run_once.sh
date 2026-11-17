#!/usr/bin/env bash
# 可选: 用宿主机 cron 每天触发一次容器任务
# 示例 crontab (每天 23:00):
#   0 23 * * * /path/to/tools/run_once.sh >> /var/log/aks-create-tables.log 2>&1

set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.app.yml run --rm --no-deps \
  --entrypoint /bin/sh create-tables -c "python app.py"
