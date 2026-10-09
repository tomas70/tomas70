#!/usr/bin/env bash
# update.sh — parsisiunčia VISUS naujausius failus iš GitHub, patikrina, kad
# jie importuojasi, ir tik tada perkrauna botą.
#
# Paleidimas: bash /Users/tomassipelis/trading-bot/update.sh
#
# Visada naudok ŠĮ skriptą atnaujinimui, ne pavienius curl komandas — botas
# yra vienas Python paketas, ir dalinis atnaujinimas (kai kurie failai nauji,
# kiti seni) sukelia importo klaidas arba KeyError, kurie kartais tylūs
# (žr. main.py:/log — jei tik main.py atnaujintas, o analysis/trade_logger.py
# ne, /log komanda luš su "KeyError: 'baseline_wr'").

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="https://raw.githubusercontent.com/tomas70/tomas70/claude/remote-control-5n0vxb/trading-bot"
VENV_PY="$DIR/.venv/bin/python"
SELF="$DIR/update.sh"

# Self-update first. Without this, a stale local copy of update.sh keeps
# running its OWN old file list and old logic forever — nothing else ever
# re-downloads update.sh itself, so a fix pushed to this script silently
# never reaches a Mac that's still running yesterday's copy of it.
if [ -z "${UPDATE_SH_REEXEC:-}" ]; then
    TMP_SELF="$(mktemp)"
    if curl -fsSL "$BASE/update.sh?$(date +%s)" -o "$TMP_SELF" && [ -s "$TMP_SELF" ]; then
        if ! cmp -s "$TMP_SELF" "$SELF"; then
            echo "🔁 update.sh pats turi naujesnę versiją — persileidžiu..."
            cp "$TMP_SELF" "$SELF"
            chmod +x "$SELF"
            rm -f "$TMP_SELF"
            exec env UPDATE_SH_REEXEC=1 bash "$SELF" "$@"
        fi
    fi
    rm -f "$TMP_SELF"
fi

echo "⬇️  Atnaujinami failai į $DIR ..."

mkdir -p "$DIR/analysis" "$DIR/notifications" "$DIR/logs" "$DIR/tests"

FILES=(
    main.py
    config.py
    requirements.txt
    run_main.sh
    setup_autostart.sh
    analysis/__init__.py
    analysis/market_data.py
    analysis/evedex_account.py
    analysis/paper_tracker.py
    analysis/pair_selection.py
    notifications/__init__.py
    notifications/telegram_bot.py
    notifications/bot_commands.py
    tests/test_paper_tracker.py
)

FAILED=0
for f in "${FILES[@]}"; do
    # Parsisiunčiama į .tmp ir perkeliama tik jei pavyko — nepavykus
    # parsisiųsti, senas (veikiantis) failas lieka nepaliestas vietoje
    # sugadinto/tuščio.
    if curl -fsSL "$BASE/$f?$(date +%s)" -o "$DIR/$f.tmp"; then
        mv "$DIR/$f.tmp" "$DIR/$f"
        echo "  ok    $f"
    else
        rm -f "$DIR/$f.tmp"
        echo "  FAIL  $f"
        FAILED=1
    fi
done

chmod +x "$DIR/run_main.sh" "$DIR/setup_autostart.sh" 2>/dev/null || true

echo "─────────────────────────────"

if [ "$FAILED" -eq 1 ]; then
    echo "❌ Kai kurie failai nenusiuntė — BOTAS NEPERKRAUNAMAS. Patikrink interneto ryšį ir bandyk dar kartą."
    exit 1
fi

echo "🔎 Tikrinami importai..."
if [ ! -x "$VENV_PY" ]; then
    echo "⚠️  .venv nerastas — praleidžiu importo patikrą (bus sukurtas paleidžiant run_main.sh)."
else
    # Run from $DIR, not from wherever the caller happened to be. `python -c`
    # puts the CURRENT directory on sys.path, not the script's, so
    # `bash ~/trading-bot/update.sh` launched from any other directory failed
    # the check with "No module named 'analysis'" and refused to restart a
    # bot whose files were in fact fine.
    if (cd "$DIR" && "$VENV_PY" -c "
import analysis.market_data, analysis.evedex_account, analysis.paper_tracker, analysis.pair_selection
import notifications.telegram_bot, notifications.bot_commands
import main
print('✅ visi moduliai importuojasi')
"); then
        :
    else
        echo "❌ IMPORTO KLAIDA — BOTAS NEPERKRAUNAMAS, kad neliktų sustabdytas. Parodyk šią klaidą, kad pataisytume."
        exit 1
    fi
fi

echo "🔄 Perkraunamas botas..."
pkill -9 -f main.py 2>/dev/null || true
sleep 35

if pgrep -fl main.py > /dev/null; then
    echo "✅ BOTAS VEIKIA"
else
    echo "❌ Botas nepasileido po perkrovimo — patikrink logs/ arba paleisk rankiniu būdu:"
    echo "   cd $DIR && .venv/bin/python main.py"
fi
