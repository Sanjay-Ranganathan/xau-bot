#!/bin/bash
# =============================================================
# Deploy to Fly.io — Free, No Credit Card, Always-On
# =============================================================
set -e

echo "============================================"
echo "  Deploy XAUUSD Bot to Fly.io (Free)"
echo "============================================"
echo ""

# Check prerequisites
command -v flyctl >/dev/null 2>&1 || {
    echo "Installing flyctl..."
    curl -L https://fly.io/install.sh | sh
    export PATH="$HOME/.fly/bin:$PATH"
}

command -v git >/dev/null 2>&1 || {
    echo "Installing git..."
    apt-get update && apt-get install -y git
}

command -v docker >/dev/null 2>&1 || {
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
}

echo ""
echo "[1/5] Login to Fly.io"
echo "  → A browser will open. Create a free account (no credit card needed)."
echo "  → Or run: flyctl auth signup"
flyctl auth login

echo ""
echo "[2/5] Initialize app"
cd "$(dirname "$0")"
flyctl apps create xau-bot --org personal 2>/dev/null || echo "  App already exists"

echo ""
echo "[3/5] Launch"
flyctl launch --copy-config --yes

echo ""
echo "[4/5] Deploy"
flyctl deploy

echo ""
echo "[5/5] Done!"
echo ""
echo "============================================"
echo "  Bot is LIVE on Fly.io!"
echo "============================================"
echo ""
echo "  Dashboard: https://xau-bot.fly.dev"
echo "  Logs:      flyctl logs"
echo "  Status:    flyctl status"
echo "  Restart:   flyctl restart"
echo ""
echo "  Config is in config.json — edit and redeploy:"
echo "    flyctl deploy"
echo ""
