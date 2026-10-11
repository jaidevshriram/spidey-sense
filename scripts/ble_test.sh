#!/bin/zsh
# Bluetooth check: connects to DataFeelCE, flashes the Dot green. Run from Terminal.app.
cd "$(dirname "$0")/.." && source .venv/bin/activate && mkdir -p logs
python3 -u haptics/ble_test.py "$@" 2>&1 | grep --line-buffered -v objc | tee logs/ble_test.txt
