#!/bin/zsh
# Neon via the Companion phone (Wi-Fi): find it, measure fps / gaze rate / delay, then a live view.
#   ./scripts/neon_check.sh               auto-discover
#   ./scripts/neon_check.sh 192.168.1.23  phone IP from the Companion app (if discovery is blocked)
#   ./scripts/neon_check.sh --mock        no device
cd "$(dirname "$0")/.." && source .venv/bin/activate
python3 -u neon/check_companion.py "$@" --view 2>&1 | grep --line-buffered -v objc
