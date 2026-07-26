#!/bin/bash
# Deploy XAUUSD Trading Bot to VPS
set -e

echo "=== XAUUSD Bot Deployment ==="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

BOT_DIR="/home/sanjay/xau_bot"

# 1. Check Python
echo -e "${YELLOW}[1/6] Checking Python...${NC}"
python3 --version || { echo -e "${RED}Python3 not found!${NC}"; exit 1; }
pip3 install --quiet flask polars numpy requests websocket-client python-dateutil gunicorn 2>/dev/null || \
pip install --quiet flask polars numpy requests websocket-client python-dateutil gunicorn 2>/dev/null
echo -e "${GREEN}Python deps installed${NC}"

# 2. Create directories
echo -e "${YELLOW}[2/6] Creating directories...${NC}"
mkdir -p "$BOT_DIR/logs" "$BOT_DIR/data"
echo -e "${GREEN}Directories ready${NC}"

# 3. Default config if not exists
echo -e "${YELLOW}[3/6] Checking config...${NC}"
if [ ! -f "$BOT_DIR/config.json" ]; then
    cat > "$BOT_DIR/config.json" << 'CONF'
{
  "SIGNALS": ["h1_trend", "sweep"],
  "MIN_AGREE": 2,
  "SL_PTS": 7.0,
  "TP_RATIO": 1.0,
  "TP_PTS": 7.0,
  "MAX_DAILY_TRADES": 8,
  "SESSION_START": 8,
  "SESSION_END": 13,
  "TRADE_FULLSCREEN": false,
  "DASH_PORT": 8080,
  "TG_ENABLED": false,
  "TG_BOT_TOKEN": "",
  "TG_CHAT_ID": "",
  "CANDLE_FILE": "/home/sanjay/xauusd_5min_1yr.csv"
}
CONF
    echo -e "${GREEN}Default config created at $BOT_DIR/config.json${NC}"
else
    echo -e "${GREEN}Config exists${NC}"
fi

# 4. Systemd service
echo -e "${YELLOW}[4/6] Installing systemd service...${NC}"
cp "$BOT_DIR/systemd/xau-bot.service" /etc/systemd/system/xau-bot.service
systemctl daemon-reload
systemctl enable xau-bot 2>/dev/null
echo -e "${GREEN}Service installed and enabled${NC}"

# 5. Option to start
echo -e "${YELLOW}[5/6] Ready to start${NC}"
echo ""
echo "Deployment complete! To start:"
echo ""
echo "  Option A (systemd - recommended for VPS):"
echo "    sudo systemctl start xau-bot"
echo "    sudo systemctl status xau-bot"
echo "    sudo journalctl -u xau-bot -f"
echo ""
echo "  Option B (Docker):"
echo "    cd $BOT_DIR"
echo "    docker-compose up -d"
echo "    docker-compose logs -f"
echo ""
echo "  Option C (direct):"
echo "    cd $BOT_DIR"
echo "    python3 main.py paper --feed websocket"
echo ""

# 6. Dashboard URL
echo -e "${YELLOW}[6/6] Dashboard${NC}"
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo "  Dashboard: http://${IP}:8080"
echo ""
echo -e "${GREEN}=== Deployment complete ===${NC}"
