#!/bin/zsh
# Engine haptics over Bluetooth via the app WebSocket (health, ping, LED, one 0.3-intensity buzz). Terminal.app only.
cd "$(dirname "$0")/.." && source .venv/bin/activate && mkdir -p logs
python3 -u haptics/engine_link_test.py 2>&1 | grep --line-buffered -v objc | tee logs/engine_link_test.txt
