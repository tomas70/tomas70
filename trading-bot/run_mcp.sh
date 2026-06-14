#!/usr/bin/env bash
# run_mcp.sh — Claude Desktop iškviečia šį skriptą.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Sukurti venv jei dar nėra
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[trading-bot] Kuriamas venv..." >&2
    python3 -m venv "$VENV_DIR"
fi

# Visada atnaujinti pip ir paketus (greita jei jau įdiegta)
"$VENV_DIR/bin/pip" install --quiet --upgrade pip >&2
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" >&2

# Patikrinti ar mcp modulis veikia
if ! "$VENV_DIR/bin/python" -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
    echo "[trading-bot] mcp paketas nerastas, instaliuojama iš naujo..." >&2
    "$VENV_DIR/bin/pip" install --upgrade "mcp[cli]" >&2
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/mcp_server.py"
