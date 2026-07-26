#!/bin/bash
# =============================================================
# XAUUSD Bot — 24/7 watchdog (auto-restarts on crash)
# Run this in a screen/tmux session
# =============================================================
BOT_DIR="/home/sanjay/xau_bot"
LOG="$BOT_DIR/logs/bot.log"

mkdir -p "$BOT_DIR/logs" "$BOT_DIR/data"
cd "$BOT_DIR"

echo "Starting XAUUSD Bot watchdog..."
echo "Dashboard: http://localhost:8080"
echo "Logs: tail -f $LOG"
echo "Stop: Ctrl+C"
echo ""

while true; do
    echo "[$(date)] Starting bot..."
    python3 server.py 2>&1 | tee -a "$LOG"
    EXIT_CODE=$?
    echo "[$(date)] Bot exited with code $EXIT_CODE. Restarting in 10s..."
    sleep 10
done
