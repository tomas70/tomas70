#!/usr/bin/env bash
# run_mcp.sh — Claude Desktop iškviečia šį skriptą.
# Automatiškai sukuria venv ir instaliuoja priklausomybes pirmą kartą.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Sukurti venv jei dar nėra
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[trading-bot] Kuriamas venv..." >&2
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip >&2
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" >&2
    echo "[trading-bot] Instaliacija baigta." >&2
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/mcp_server.py"
