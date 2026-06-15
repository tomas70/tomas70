#!/usr/bin/env bash
# setup_autostart.sh — įdiegia Trading Bot kaip macOS launchd agentą
# Paleidimas: bash /Users/tomassipelis/trading-bot/setup_autostart.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.tradingbot.plist"
LABEL="com.tradingbot"

echo "⚙️  Diegiamas launchd agentas..."

# Sukurti logs katalogą
mkdir -p "$DIR/logs"

# Sustabdyti seną agentą jei veikia
launchctl unload "$PLIST" 2>/dev/null || true

# Sukurti plist failą
cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${DIR}/run_main.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>${DIR}/logs/bot.log</string>
    <key>StandardErrorPath</key>
    <string>${DIR}/logs/bot.log</string>
</dict>
</plist>
EOF

# Sustabdyti rankiniu būdu paleistą main.py jei veikia
pkill -9 -f "main.py" 2>/dev/null || true
sleep 2

# Aktyvuoti agentą
launchctl load "$PLIST"

echo ""
echo "✅ Trading Bot paleistas automatiškai!"
echo ""
echo "   Logai:    tail -f ${DIR}/logs/bot.log"
echo "   Sustabdyti: launchctl stop ${LABEL}"
echo "   Paleisti:   launchctl start ${LABEL}"
echo "   Išjungti:  launchctl unload ${PLIST}"
echo ""
echo "⚠️  Palaukite 35 sek kol Telegram sesija atsijungs, tada siųskite /scan"
