#!/usr/bin/env bash
# run_main.sh — automatinis bot paleidimas (standalone / auto mode)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Sukurti venv + instaliuoti jei dar nėra
if [ ! -f "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

# Telegram credentials (arba skaityk iš .env)
# export TELEGRAM_BOT_TOKEN="tavo_token"
# export TELEGRAM_CHAT_ID="tavo_chat_id"

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/main.py"
