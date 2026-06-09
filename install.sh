#!/usr/bin/env bash
# Token Status — macOS / Linux installer
set -e
cd "$(dirname "$0")"

echo "[Token Status] Creating virtualenv..."
python3 -m venv .venv

echo "[Token Status] Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo ""
echo "[Token Status] Install complete. Run ./run.sh to start."
