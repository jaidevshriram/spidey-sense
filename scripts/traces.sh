#!/bin/zsh
# Trace viewer for the engine's per-call Haiku traces (backend/logs/runs/<run>/, see backend/spidey/trace.py).
#   ./scripts/traces.sh            http://localhost:8096/  (calls as cards)  +  /live (live scene, gaze, boxes, trigger states)
#   ./scripts/traces.sh lan        same, reachable from the phone / a second screen on the same Wi-Fi
#   PORT=8097 ./scripts/traces.sh  another port;  TRACE_DIR=/path/to/runs  another runs dir
# The desk test page POSTs ground truth (which picture is shown) to http://localhost:8096/api/truth.
cd "$(dirname "$0")/.." || exit 1
PORT=${PORT:-8096}
HOST=127.0.0.1
if [[ "$1" == "lan" ]]; then
  HOST=0.0.0.0
  IP=$(ipconfig getifaddr en0 2>/dev/null || echo "?")
  echo "LAN: http://$IP:$PORT/   live: http://$IP:$PORT/live"
fi
cd backend && exec ../.venv/bin/python -u -m spidey.trace_viewer --port "$PORT" --host "$HOST" \
  --dir "${TRACE_DIR:-logs/runs}"
