#!/bin/zsh
# Haptic Playground. Run from Terminal.app (Bluetooth). Args pass through: --transport usb | --mock
cd "$(dirname "$0")/.." && source .venv/bin/activate
python3 -u backend/playground/server.py "$@" 2>&1 | grep --line-buffered -v objc
