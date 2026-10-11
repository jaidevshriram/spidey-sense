#!/bin/zsh
# Bluetooth trigger latency (stock vs fast path). Buzzes the Dot ~15x. Run from Terminal.app.
cd "$(dirname "$0")/.." && source .venv/bin/activate && mkdir -p logs
python3 -u haptics/ble_latency.py 2>&1 | grep --line-buffered -v objc | tee logs/ble_latency.txt
