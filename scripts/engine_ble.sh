#!/bin/zsh
# The real thing: engine driving the Dot over Bluetooth (DataFeelCE). Terminal.app only.
# Extra args pass through, e.g. --config backend/configs/config.neon.yaml
cd "$(dirname "$0")/.." && source .venv/bin/activate && mkdir -p logs
python3 -u backend/run.py --sink datafeel-ble "$@" 2>&1 | grep --line-buffered -v objc | tee logs/engine_ble.txt
