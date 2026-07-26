#!/bin/bash
# =============================================================
# XAUUSD Bot — Run on your local machine
# Keeps running, auto-restarts on crash
# =============================================================

BOT_DIR="/home/sanjay/xau_bot"
LOG="$BOT_DIR/logs/bot_local.log"
PID_FILE="$BOT_DIR/logs/bot.pid"

mkdir -p "$BOT_DIR/logs" "$BOT_DIR/data"

# Kill existing if running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null
    sleep 1
fi

echo "============================================"
echo "  XAUUSD Paper Trading Bot"
echo "============================================"
echo ""
echo "  Dashboard: http://localhost:8080"
echo "  Strategy:  h1_trend + sweep"
echo "  Session:   London (08:00-13:00 UTC)"
echo "  SL/TP:     7pt / 10.5pt (1:1.5)"
echo "  Polling:   every 5 min"
echo ""

cd "$BOT_DIR"

# Run in background with nohup
nohup python3 server.py >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"

echo "  Bot PID: $(cat $PID_FILE)"
echo "  Logs:    tail -f $LOG"
echo ""
echo "  Stop:    kill \$(cat $PID_FILE)"
echo "============================================"
