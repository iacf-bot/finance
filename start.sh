#!/usr/bin/env bash
# Start IACF Finance in the background (no systemd needed).
set -euo pipefail
cd "$(dirname "$0")"
umask 077
mkdir -p data/logs
chmod 700 data
PIDFILE=data/server.pid
if [[ -f $PIDFILE ]] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
  echo "Already running (pid $(cat $PIDFILE))"; exit 0
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
# optional settings: HOST, PORT, DATA_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS (legacy FINANCE_HOSTS/FINANCE_PORT also work)
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
export FINANCE_HOSTS="${FINANCE_HOSTS:-${HOST:-127.0.0.1,100.83.220.110}}"
export FINANCE_PORT="${FINANCE_PORT:-${PORT:-8088}}"
nohup .venv/bin/python -m app.serve >> data/logs/server.log 2>&1 &
echo $! > $PIDFILE
sleep 2
if kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
  echo "Started (pid $(cat $PIDFILE)). Log: data/logs/server.log"
  grep -E "Listening on|Could not bind" data/logs/server.log | tail -n 4 || true
else
  echo "Failed to start; see data/logs/server.log"; tail -n 20 data/logs/server.log; exit 1
fi
