#!/bin/bash
# Watchdog entry for the live screener (user crontab, every 5 min): start it unless it already runs.
# The flock is held for the screener's whole life, so a second start exits at once.
cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs
exec 9>logs/live.lock
flock -n 9 || exit 0
set -a; . "$HOME/projects/trenches/.env"; set +a
echo "[$(date -u +%FT%TZ)] boot" >> logs/live_boot.log
.venv/bin/python -m tlive.run >> logs/live.log 2>&1
echo "[$(date -u +%FT%TZ)] exit $?" >> logs/live_boot.log
