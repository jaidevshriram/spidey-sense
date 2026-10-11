#!/bin/zsh
# One-time setup: Python 3.12 venv with everything installed (editable).
set -e
cd "$(dirname "$0")/.."
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e haptics/vendor/datafeel-python -e haptics -e neon -e backend
echo "Done. Next: source .venv/bin/activate"
