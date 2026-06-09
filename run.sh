#!/usr/bin/env bash
# Token Status — macOS / Linux launcher
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[Token Status] .venv not found. Run ./install.sh first."
    exit 1
fi

exec .venv/bin/python launcher.py "$@"
