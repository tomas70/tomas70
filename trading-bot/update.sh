#!/usr/bin/env bash
# update.sh — parsisiunčia naujausius failus iš GitHub
# Paleidimas: bash /Users/tomassipelis/trading-bot/update.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="https://raw.githubusercontent.com/tomas70/tomas70/claude/remote-control-5n0vxb/trading-bot"

echo "⬇️  Atnaujinami failai į $DIR ..."

mkdir -p "$DIR/analysis" "$DIR/ai" "$DIR/notifications" "$DIR/risk" "$DIR/logs"

curl -fsSL "$BASE/main.py"                     -o "$DIR/main.py"
curl -fsSL "$BASE/mcp_server.py"               -o "$DIR/mcp_server.py"
curl -fsSL "$BASE/config.py"                   -o "$DIR/config.py"
curl -fsSL "$BASE/requirements.txt"            -o "$DIR/requirements.txt"
curl -fsSL "$BASE/run_mcp.sh"                  -o "$DIR/run_mcp.sh"
curl -fsSL "$BASE/run_main.sh"                 -o "$DIR/run_main.sh"

curl -fsSL "$BASE/analysis/__init__.py"        -o "$DIR/analysis/__init__.py"
curl -fsSL "$BASE/analysis/market_data.py"     -o "$DIR/analysis/market_data.py"
curl -fsSL "$BASE/analysis/smc.py"             -o "$DIR/analysis/smc.py"
curl -fsSL "$BASE/analysis/ross_hook.py"       -o "$DIR/analysis/ross_hook.py"
curl -fsSL "$BASE/analysis/multi_timeframe.py" -o "$DIR/analysis/multi_timeframe.py"

curl -fsSL "$BASE/ai/__init__.py"              -o "$DIR/ai/__init__.py"
curl -fsSL "$BASE/ai/claude_analyst.py"        -o "$DIR/ai/claude_analyst.py"

curl -fsSL "$BASE/notifications/__init__.py"   -o "$DIR/notifications/__init__.py"
curl -fsSL "$BASE/notifications/telegram_bot.py"  -o "$DIR/notifications/telegram_bot.py"
curl -fsSL "$BASE/notifications/bot_commands.py"  -o "$DIR/notifications/bot_commands.py"

curl -fsSL "$BASE/risk/__init__.py"            -o "$DIR/risk/__init__.py"
curl -fsSL "$BASE/risk/position_sizer.py"      -o "$DIR/risk/position_sizer.py"

chmod +x "$DIR/run_mcp.sh" "$DIR/run_main.sh"

echo "✅ Visi failai atnaujinti!"
echo "   .env failas NEPALIESTAS — tavo API raktai saugūs."
