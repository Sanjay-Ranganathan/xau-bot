#!/bin/bash
# =============================================================
# XAUUSD Trading Bot — One-shot deploy for fresh Ubuntu VPS
# Usage: bash setup_bot.sh
# =============================================================
set -e

echo "============================================"
echo "  XAUUSD Bot — Full Setup"
echo "============================================"

# 1. System deps
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv curl > /dev/null 2>&1
echo "  Done"

# 2. Project setup
echo "[2/5] Setting up project..."
BOT_DIR="/root/xau_bot"
mkdir -p "$BOT_DIR"/{logs,data}

# If backup exists, extract it
if [ -f /tmp/xau_bot_backup.tar.gz ]; then
    cd /root
    tar -xzf /tmp/xau_bot_backup.tar.gz
    echo "  Extracted from backup"
elif [ -d /home/*/xau_bot ]; then
    cp -r /home/*/xau_bot/* "$BOT_DIR/"
    echo "  Copied from home"
else
    echo "  No source found — place bot files in $BOT_DIR"
    exit 1
fi

# 3. Python deps
echo "[3/5] Installing Python packages..."
cd "$BOT_DIR"
pip3 install --quiet -r requirements.txt
echo "  Done"

# 4. Config
echo "[4/5] Setting up config..."
if [ ! -f "$BOT_DIR/config.json" ]; then
    cat > "$BOT_DIR/config.json" << 'EOF'
{
  "SIGNALS": ["h1_trend", "sweep"],
  "MIN_AGREE": 1,
  "SL_PTS": 7.0,
  "TP_RATIO": 1.5,
  "TP_PTS": 10.5,
  "MAX_DAILY_TRADES": 8,
  "MAX_CONCURRENT_TRADES": 3,
  "COOLDOWN_CANDLES": 2,
  "SESSION_FILTER": 1,
  "SESSION_START": 8,
  "SESSION_END": 13,
  "HOLD_CANDLES": 20,
  "SWEEP_LEVEL": 0,
  "INITIAL_BALANCE": 10000.0,
  "TG_ENABLED": false,
  "TG_BOT_TOKEN": "",
  "TG_CHAT_ID": "",
  "DASH_PORT": 8080,
  "CANDLE_FILE": "/root/xau_bot/data/xauusd_5min_1yr.csv"
}
EOF
fi
echo "  Done"

# 5. Systemd service
echo "[5/5] Installing systemd service..."
cat > /etc/systemd/system/xau-bot.service << 'EOF'
[Unit]
Description=XAUUSD Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/xau_bot
ExecStart=/usr/bin/python3 /root/xau_bot/main.py paper --feed websocket --log-level INFO
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1
Environment=TZ=UTC

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xau-bot
echo "  Done"

echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  Start bot:   systemctl start xau-bot"
echo "  Stop bot:    systemctl stop xau-bot"
echo "  Logs:        journalctl -u xau-bot -f"
echo "  Status:      systemctl status xau-bot"
echo "  Dashboard:   http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8080"
echo ""
echo "  Config:      /root/xau_bot/config.json"
echo "  Edit & restart to change settings."
echo ""
