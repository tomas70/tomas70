#!/usr/bin/env bash
# run_mcp.sh — Claude Desktop iškviečia šį skriptą.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ─── Find Python 3.10+ (required by mcp package) ─────────────────────────────
# Check Homebrew locations first (Apple Silicon: /opt/homebrew, Intel: /usr/local)
find_python() {
    for py in \
        /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.11 \
        /opt/homebrew/bin/python3.10 \
        /usr/local/bin/python3.13 \
        /usr/local/bin/python3.12 \
        /usr/local/bin/python3.11 \
        /usr/local/bin/python3.10 \
        python3.13 python3.12 python3.11 python3.10; do
        if command -v "$py" &>/dev/null; then
            version=$("$py" -c "import sys; print(sys.version_info[:2])")
            if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python 2>/dev/null) || {
    echo "[trading-bot] KLAIDA: Python 3.10+ nerastas." >&2
    echo "[trading-bot] Instaliuok su Homebrew:" >&2
    echo "[trading-bot]   brew install python@3.11" >&2
    exit 1
}

echo "[trading-bot] Naudojamas Python: $PYTHON ($("$PYTHON" --version 2>&1))" >&2

# ─── Recreate venv if it was built with wrong Python version ──────────────────
if [ -f "$VENV_DIR/bin/python" ]; then
    VENV_VER=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || echo "(0, 0)")
    OK=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo "False")
    if [ "$OK" != "True" ]; then
        echo "[trading-bot] Senas venv (Python $VENV_VER) — trinamas ir kuriamas iš naujo..." >&2
        rm -rf "$VENV_DIR"
    fi
fi

# ─── Create venv if missing ───────────────────────────────────────────────────
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[trading-bot] Kuriamas venv su $PYTHON..." >&2
    "$PYTHON" -m venv "$VENV_DIR"
fi

# ─── Always update packages ───────────────────────────────────────────────────
"$VENV_DIR/bin/pip" install --quiet --upgrade pip >&2
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" >&2

# ─── Verify mcp is importable ─────────────────────────────────────────────────
if ! "$VENV_DIR/bin/python" -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
    echo "[trading-bot] mcp paketas nerastas, instaliuojama iš naujo..." >&2
    "$VENV_DIR/bin/pip" install --upgrade "mcp[cli]" >&2
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/mcp_server.py"
