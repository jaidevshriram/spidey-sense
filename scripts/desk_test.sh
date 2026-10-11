#!/bin/zsh
# Desk test helper.
#   ./scripts/desk_test.sh              serve the test page (backend/desk_test) on :8095 and print the URL
#   ./scripts/desk_test.sh prerun [A B C D1 D2] [--max-side 512]
#                                       pre-run the tests without hardware (mock scene camera, real Haiku,
#                                       console sink, no server) -> vlm/desk_test_results.md, logs/desk_sim/
#   ./scripts/desk_test.sh check        offline engine checks (radar hook, pause, status, gaze, trend)
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
case "$1" in
  prerun) shift; exec $PY -u backend/desk_test/run_sim.py "$@" ;;
  check)  shift; exec $PY -u backend/desk_test/test_engine_v3.py "$@" ;;
esac
PORT=${PORT:-8095}
IP=$(ipconfig getifaddr en0 2>/dev/null || echo 127.0.0.1)
echo "Desk test page:  http://localhost:$PORT/   (LAN: http://$IP:$PORT/)"
echo "  Tests A/B: pictures on the right third (6 s each, N = next, P = pause).  Tests C/D: http://localhost:$PORT/?test=C"
echo "  Full screen it on the monitor in front of you. Ctrl-C to stop."
exec $PY -m http.server "$PORT" --bind 0.0.0.0 --directory backend/desk_test
