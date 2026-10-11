#!/bin/bash
# Xcode scheme Run pre-action: if nothing listens on TCP 8765, start the demo engine (fake source +
# console sink: no Bluetooth) in the background so the app is live as soon as you hit Run.
# BLE runs (--sink datafeel-ble) still go through Terminal.app. Never blocks or fails the build.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SRCROOT:-$HERE/..}/../.." 2>/dev/null && pwd)"
mkdir -p "$REPO/logs" 2>/dev/null
LOG="$REPO/logs/xcode_preaction.log"
{
  echo "[$(date '+%F %T')] pre-action (repo $REPO)"
  if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  engine already running on 8765, skip"
  elif [ ! -x "$REPO/.venv/bin/python" ]; then
    echo "  no $REPO/.venv: run ./scripts/setup.sh first; not starting the engine"
  else
    cd "$REPO" && nohup ./.venv/bin/python backend/run.py > logs/engine_xcode.log 2>&1 < /dev/null &
    echo "  started demo engine (pid $!): logs/engine_xcode.log"
  fi
} >> "$LOG" 2>&1
exit 0
