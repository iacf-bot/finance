#!/usr/bin/env bash
# Optional supervisor: restarts the server if it dies. Run:  nohup ./watchdog.sh >/dev/null 2>&1 &
cd "$(dirname "$0")"
while true; do
  if ! { [[ -f data/server.pid ]] && kill -0 "$(cat data/server.pid)" 2>/dev/null; }; then
    echo "$(date '+%F %T') restarting" >> data/logs/watchdog.log; ./start.sh >> data/logs/watchdog.log 2>&1 || true
  fi
  sleep 30
done
