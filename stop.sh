#!/usr/bin/env bash
cd "$(dirname "$0")"
PIDFILE=data/server.pid
if [[ -f $PIDFILE ]] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
  kill "$(cat $PIDFILE)"; for i in $(seq 1 20); do kill -0 "$(cat $PIDFILE)" 2>/dev/null || break; sleep 0.25; done
  kill -9 "$(cat $PIDFILE)" 2>/dev/null || true
  echo "Stopped"
else
  echo "Not running"
fi
rm -f $PIDFILE
