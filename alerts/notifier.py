"""Telegram and console alert system for trade notifications."""
import logging
import requests

logger = logging.getLogger(__name__)

_config = {
    "tg_enabled": False,
    "bot_token": "",
    "chat_id": "",
}


def init_notifier(tg_enabled=False, bot_token="", chat_id=""):
    _config["tg_enabled"] = tg_enabled
    _config["bot_token"] = bot_token
    _config["chat_id"] = chat_id
    if tg_enabled:
        logger.info(f"Telegram alerts enabled (chat_id={chat_id[:8]}...)")
    else:
        logger.info("Telegram alerts disabled (console only)")


def send_alert(message):
    """Send alert to Telegram + console."""
    logger.info(message)

    if _config["tg_enabled"] and _config["bot_token"] and _config["chat_id"]:
        try:
            url = f"https://api.telegram.org/bot{_config['bot_token']}/sendMessage"
            payload = {
                "chat_id": _config["chat_id"],
                "text": message,
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")


def send_daily_summary(trades, balance, max_dd):
    """Send end-of-day summary."""
    if not trades:
        return

    wins = [t for t in trades if t["pnl_pts"] > 0]
    losses = [t for t in trades if t["pnl_pts"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in trades)
    wr = len(wins) / len(trades) * 100 if trades else 0

    msg = (
        f"📊 DAILY SUMMARY — XAUUSD\n"
        f"{'=' * 30}\n"
        f"Trades: {len(trades)} ({len(wins)}W / {len(losses)}L)\n"
        f"Win Rate: {wr:.1f}%\n"
        f"Daily PnL: ${total_pnl:+,.2f}\n"
        f"Balance: ${balance:,.2f}\n"
        f"Max DD: ${max_dd:,.2f}\n"
        f"{'=' * 30}"
    )
    send_alert(msg)
