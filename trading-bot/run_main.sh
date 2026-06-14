#!/usr/bin/env bash
# run_main.sh — automatinis bot paleidimas (standalone / auto mode)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ─── Find Python 3.10+ ────────────────────────────────────────────────────────
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
            if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python 2>/dev/null) || {
    echo "KLAIDA: Python 3.10+ nerastas. Instaliuok: brew install python@3.11"
    exit 1
}

# ─── Recreate venv if wrong Python version ────────────────────────────────────
if [ -f "$VENV_DIR/bin/python" ]; then
    OK=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo "False")
    if [ "$OK" != "True" ]; then
        echo "Senas venv su senu Python — trinamas..."
        rm -rf "$VENV_DIR"
    fi
fi

# ─── Create venv + install deps ───────────────────────────────────────────────
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "Kuriamas venv su $PYTHON..."
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py"
