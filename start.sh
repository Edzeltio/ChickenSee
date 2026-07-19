#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
# start.sh  —  Raspberry Pi launcher for the Python sensor logger.
# ════════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

echo "=== Sensor Logger (Python) ==="
echo

# ── Check Python 3 ─────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found."
    echo "        Run:  sudo apt install -y python3 python3-pip"
    exit 1
fi

# ── Install dependencies if missing ────────────────────────────────────────
if ! python3 -c "import serial" &>/dev/null; then
    echo "[setup] Installing Python dependencies..."
    pip3 install -r requirements.txt
fi

# ── Copy .env if missing ────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[setup] Created sensor/.env"
    echo "        Edit it and fill in your SUPABASE_URL and SUPABASE_KEY:"
    echo "          nano $(pwd)/.env"
    echo
fi

# ── Grant serial port permission (once) ─────────────────────────────────────
if ! groups "$USER" | grep -q dialout; then
    echo "[setup] Adding $USER to dialout group (required for serial port)..."
    sudo usermod -a -G dialout "$USER"
    echo "        Log out and back in, then re-run this script."
    exit 0
fi

# ── Start ───────────────────────────────────────────────────────────────────
echo "Starting... (Ctrl+C to stop)"
echo
python3 python/main.py
