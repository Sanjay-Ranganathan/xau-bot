"""XAUUSD Trading Bot — main entry point."""
import sys
import os
import argparse
import logging
import signal
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import StrategyConfig
from core.backtester import run_backtest, compute_stats, load_data, save_results
from core.paper_trader import PaperTrader
from feeds.mt5_feed import MT5Feed
from feeds.websocket_feed import WebSocketFeed
from alerts.notifier import init_notifier, send_alert
from dashboard.app import create_app

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_FILE = "/home/sanjay/xau_bot/logs/bot.log"


def setup_logging(level="INFO"):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE),
        ],
    )


def cmd_backtest(args):
    """Run backtester."""
    cfg = StrategyConfig()
    if args.config:
        cfg.load(args.config)

    print("Loading data...")
    df = load_data(cfg.CANDLE_FILE)
    print(f"Loaded {len(df)} candles")

    print("Computing indicators...")
    start = datetime.now()
    trades, equity, extra = run_backtest(df, cfg)
    stats = compute_stats(trades, equity, extra, len(df))
    elapsed = (datetime.now() - start).total_seconds()

    print(f"\nBacktest complete in {elapsed:.1f}s")
    print(f"{'=' * 60}")
    print(f"Strategy:     {' + '.join(cfg.SIGNALS)}")
    print(f"Min agree:    {cfg.MIN_AGREE}")
    print(f"Session:      {cfg.SESSION_START:02d}:00-{cfg.SESSION_END:02d}:00 UTC")
    print(f"SL/TP:        {cfg.SL_PTS} / {cfg.TP_PTS} pt")
    print(f"{'=' * 60}")
    print(f"Total trades: {stats['total_trades']}")
    print(f"Win rate:     {stats['win_rate']}%")
    print(f"Profit factor:{stats['pf']}")
    print(f"Avg PnL:      {stats['avg_pts']} pts")
    print(f"Total PnL:    {stats['total_pnl_pts']} pts (${stats['total_pnl_usd']:,})")
    print(f"Max DD:       ${stats['max_dd_usd']:,.2f}")
    print(f"Trades/day:   {stats['trades_per_day']}")
    print(f"{'=' * 60}")

    for name, info in stats["session_info"].items():
        print(f"  {name:8s}: {info['total']:4d} trades, {info['wr']:5.1f}% WR")

    print("\nMonthly:")
    for m in sorted(stats["monthly"].keys()):
        v = stats["monthly"][m]
        print(f"  {m:10s}  {v['trades']:6d}  {v['wr']:5.1f}%  ${v['pnl']:>10,.2f}")

    save_results(trades, stats, equity, args.output or "backtest_results.json")


def cmd_paper(args):
    """Run paper trader with live data feed."""
    cfg = StrategyConfig()
    if args.config:
        cfg.load(args.config)

    init_notifier(cfg.TG_ENABLED, cfg.TG_BOT_TOKEN, cfg.TG_CHAT_ID)

    # Connect to data feed
    feed = None
    if args.feed == "mt5":
        feed = MT5Feed(
            symbol=args.symbol or "XAUUSD",
            server=args.server,
            login=args.login,
            password=args.password,
        )
    elif args.feed == "websocket":
        feed = WebSocketFeed(feed_name=args.ws_feed or "twelvedata")

    if feed is None:
        print("No data feed available. Use --feed mt5 or --feed websocket")
        sys.exit(1)

    print(f"Connecting to {args.feed} feed...")
    if not feed.connect():
        print("Failed to connect to data feed. Retrying...")
        if not feed.connect():
            print("Could not connect. Exiting.")
            sys.exit(1)
    print("Connected!")

    trader = PaperTrader(feed, cfg)
    trader.start()

    # Start dashboard
    app = create_app(paper_trader=trader, cfg=cfg)

    # Graceful shutdown
    def shutdown(sig, frame):
        print("\nShutting down...")
        trader.stop()
        feed.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"Dashboard: http://localhost:{cfg.DASH_PORT}")
    print(f"Strategy:  {' + '.join(cfg.SIGNALS)}")
    print(f"SL/TP:     {cfg.SL_PTS}/{cfg.TP_PTS} pt")
    print(f"Session:   {cfg.SESSION_START:02d}:00-{cfg.SESSION_END:02d}:00 UTC")
    print("Bot running... Press Ctrl+C to stop.\n")

    send_alert(
        f"🤖 XAUUSD Bot Started\n"
        f"Strategy: {' + '.join(cfg.SIGNALS)}\n"
        f"SL/TP: {cfg.SL_PTS}/{cfg.TP_PTS}\n"
        f"Feed: {args.feed}"
    )

    app.run(host=cfg.DASH_HOST, port=cfg.DASH_PORT, debug=False)


def cmd_status(args):
    """Check bot status from saved state."""
    state_file = "/home/sanjay/xau_bot/data/paper_state.json"
    if not os.path.exists(state_file):
        print("No paper trading state found. Bot may not be running.")
        return

    with open(state_file) as f:
        state = json.load(f)

    print(f"Balance:    ${state.get('balance', 0):,.2f}")
    print(f"Peak:       ${state.get('peak', 0):,.2f}")
    print(f"Max DD:     ${state.get('max_dd', 0):,.2f}")
    print(f"Trades:     {len(state.get('trades', []))}")
    print(f"Last update:{state.get('last_update', '—')}")

    if state.get("position"):
        pos = state["position"]
        side = "BUY" if pos["dir"] == 1 else "SELL"
        print(f"Open:       {side} @ {pos['entry']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="XAUUSD Trading Bot")
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # Backtest
    bt = sub.add_parser("backtest", help="Run backtester")
    bt.add_argument("-c", "--config", help="Config JSON file")
    bt.add_argument("-o", "--output", default="backtest_results.json", help="Output file")
    bt.add_argument("--log-level", default="WARNING")

    # Paper trade
    pt = sub.add_parser("paper", help="Run paper trader")
    pt.add_argument("-c", "--config", help="Config JSON file")
    pt.add_argument("--feed", choices=["mt5", "websocket"], default="websocket")
    pt.add_argument("--ws-feed", default="twelvedata", help="WebSocket feed name")
    pt.add_argument("--symbol", default="XAUUSD")
    pt.add_argument("--server", help="MT5 server")
    pt.add_argument("--login", help="MT5 login")
    pt.add_argument("--password", help="MT5 password")
    pt.add_argument("--log-level", default="INFO")

    # Status
    sub.add_parser("status", help="Check bot status")

    args = parser.parse_args()

    if args.command == "backtest":
        setup_logging(args.log_level)
        cmd_backtest(args)
    elif args.command == "paper":
        setup_logging(args.log_level)
        cmd_paper(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


# Gunicorn entry point — creates a no-op app for health checks
# Real paper trading runs via `python3 main.py paper`
app = create_app()

if __name__ == "__main__":
    import json
    main()
