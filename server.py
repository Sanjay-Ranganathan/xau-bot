"""Koyeb-compatible server — dashboard + paper trading in one process.
Runs as a web service. Paper trader polls every 5 min.
External cron (cron-job.org) pings /health to prevent scale-to-zero."""
import os
import sys
import json
import time
import threading
import logging
from datetime import datetime

import numpy as np
import polars as pl
import requests
from flask import Flask, jsonify, render_template_string

from core.config import StrategyConfig
from core.indicators import compute_all
from core.signals import generate_signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

cfg = StrategyConfig()
cfg.load("config.json")

# === STATE ===
STATE_FILE = "data/paper_state.json"
_state = {
    "balance": cfg.INITIAL_BALANCE,
    "peak": cfg.INITIAL_BALANCE,
    "max_dd": 0,
    "trades": [],
    "open_pos": [],
    "daily_trades": {},
    "last_cooldown": {},
    "last_tick": None,
}
_last_candles = []
_ind = None
_lock = threading.Lock()


def _load_state():
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
        logger.info(f"State loaded: balance=${_state['balance']:,.2f}, {len(_state['trades'])} trades")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.info("No previous state, starting fresh")


def _save_state():
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"State save error: {e}")


# === PRICE FETCHING (via free APIs, no WebSocket needed) ===
import subprocess

def _curl_json(url):
    """Fetch JSON via curl (works when requests doesn't)."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "10", url, "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout)
    except Exception:
        pass
    return None

def fetch_latest_candles(count=200):
    """Fetch recent 5-min OHLCV candles from free APIs."""
    candles = []

    # Yahoo Finance (free, no API key needed)
    try:
        range_param = "2d" if count <= 200 else "5d"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=5m&range={range_param}"
        data = _curl_json(url)
        if data and "chart" in data and data["chart"]["result"]:
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            ohlcv = result["indicators"]["quote"][0]
            for idx, ts in enumerate(timestamps):
                o = ohlcv["open"][idx]
                h = ohlcv["high"][idx]
                lo = ohlcv["low"][idx]
                c = ohlcv["close"][idx]
                v = ohlcv.get("volume", [1])[idx] or 1
                if o and h and lo and c:
                    candles.append({
                        "time": datetime.utcfromtimestamp(ts),
                        "open": float(o),
                        "high": float(h),
                        "low": float(lo),
                        "close": float(c),
                        "volume": float(v),
                    })
            logger.info(f"Fetched {len(candles)} candles from Yahoo Finance")
            return candles
    except Exception as e:
        logger.warning(f"Yahoo Finance failed: {e}")

    # Fallback: TwelveData
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {"symbol": "XAU/USD", "interval": "5min", "outputsize": count, "apikey": "demo"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "values" in data:
                for v in reversed(data["values"]):
                    candles.append({
                        "time": datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S"),
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "volume": float(v.get("volume", 1)),
                    })
                logger.info(f"Fetched {len(candles)} candles from TwelveData")
                return candles
    except Exception as e:
        logger.warning(f"TwelveData failed: {e}")

    return candles


def fetch_live_price():
    """Get just the latest price."""
    # Yahoo Finance via curl
    try:
        data = _curl_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d")
        if data and data.get("chart", {}).get("result"):
            return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        pass
    return 0


# === PAPER TRADING LOGIC ===
def run_tick():
    """Process one tick — fetch data, compute indicators, check signals."""
    global _ind, _last_candles

    with _lock:
        candles = fetch_latest_candles(300)
        if not candles:
            logger.warning("No candle data available")
            return {"status": "no_data"}

        _last_candles = candles

        df = pl.DataFrame({
            "time": [c["time"].isoformat() for c in candles],
            "open": [c["open"] for c in candles],
            "high": [c["high"] for c in candles],
            "low": [c["low"] for c in candles],
            "close": [c["close"] for c in candles],
            "volume": [c.get("volume", 1.0) for c in candles],
        })

        _ind = compute_all(df)
        n = _ind["n"]
        i = n - 1

        # Check exits
        _check_exits(i)

        # Signal generation
        sess = _ind["sess_id"][i]
        did = _ind["date_id"][i]
        sk = (str(did), str(sess))

        # Session filter
        if cfg.SESSION_FILTER >= 0 and sess != cfg.SESSION_FILTER:
            return {"status": "outside_session", "session": sess}

        hr = _ind["hour_of_day"][i]
        if cfg.SESSION_FILTER < 0 and (hr < cfg.SESSION_START or hr >= cfg.SESSION_END):
            return {"status": "outside_hours", "hour": hr}

        # Cooldown
        last_exit = _state["last_cooldown"].get(sk, -999)
        if last_exit > i - cfg.COOLDOWN_CANDLES:
            return {"status": "cooldown"}

        # Daily limit
        if sk not in _state["daily_trades"]:
            _state["daily_trades"][sk] = 0
        if _state["daily_trades"][sk] >= cfg.MAX_DAILY_TRADES:
            return {"status": "daily_limit"}

        # Max positions
        if len(_state["open_pos"]) >= cfg.MAX_CONCURRENT_TRADES:
            return {"status": "max_positions"}

        # Same session check
        if any(p["session"] == str(sess) for p in _state["open_pos"]):
            return {"status": "session_occupied"}

        # Generate signal
        trade_ok, direction, agree = generate_signals(
            _ind, i, cfg.SIGNALS, cfg.SWEEP_LEVEL, cfg.MIN_AGREE
        )

        result = {
            "status": "signal_check",
            "time": str(_ind["dates_parsed"][i]),
            "price": float(_ind["c"][i]),
            "rsi": round(float(_ind["rsi14"][i]), 1),
            "h1_trend": int(_ind["h1_trend"][i]),
            "trade": False,
        }

        if trade_ok:
            entry = _ind["c"][i]
            pos = {
                "dir": direction,
                "entry": entry,
                "entry_i": i,
                "entry_time": str(_ind["dates_parsed"][i]),
                "sl": cfg.SL_PTS,
                "tp": cfg.TP_PTS,
                "session": str(sess),
                "date_id": str(did),
            }
            _state["open_pos"].append(pos)
            _state["daily_trades"][sk] += 1
            _save_state()

            side = "BUY" if direction == 1 else "SELL"
            result["trade"] = True
            result["direction"] = side
            result["entry"] = entry
            result["agree"] = agree
            logger.info(f"📈 SIGNAL: {side} @ {entry:.2f} (agree={agree})")

        _state["last_tick"] = datetime.now().isoformat()
        return result


def _check_exits(i):
    """Check all open positions for exits."""
    closed = []
    for pi, pos in enumerate(_state["open_pos"]):
        sl = pos["sl"]
        tp = pos["tp"]
        h = _ind["h"][i]
        lo = _ind["l"][i]

        d = pos["dir"]
        hit_sl = (lo <= pos["entry"] - sl) if d == 1 else (h >= pos["entry"] + sl)
        hit_tp = (h >= pos["entry"] + tp) if d == 1 else (lo <= pos["entry"] - tp)

        exit_price = None
        exit_reason = None

        if hit_sl and hit_tp:
            if _ind["c"][i] > _ind["o"][i]:
                exit_price = pos["entry"] + (tp if d == 1 else -sl)
                exit_reason = "tp" if d == 1 else "sl"
            else:
                exit_price = pos["entry"] + (-tp if d == -1 else -sl)
                exit_reason = "tp" if d == -1 else "sl"
        elif hit_sl:
            exit_price = pos["entry"] - sl if d == 1 else pos["entry"] + sl
            exit_reason = "sl"
        elif hit_tp:
            exit_price = pos["entry"] + tp if d == 1 else pos["entry"] - tp
            exit_reason = "tp"
        elif (i - pos["entry_i"]) >= cfg.HOLD_CANDLES:
            exit_price = _ind["c"][i]
            exit_reason = "timeout"

        if exit_price is not None:
            pnl_pts = (exit_price - pos["entry"]) * d
            pnl_usd = pnl_pts * 100
            _state["balance"] += pnl_usd
            _state["peak"] = max(_state["peak"], _state["balance"])
            dd = _state["peak"] - _state["balance"]
            _state["max_dd"] = max(_state["max_dd"], dd)

            trade = {
                "entry_time": pos["entry_time"],
                "exit_time": str(_ind["dates_parsed"][i]),
                "dir": "BUY" if d == 1 else "SELL",
                "entry": pos["entry"],
                "exit": round(exit_price, 2),
                "pnl_pts": round(pnl_pts, 2),
                "pnl_usd": round(pnl_usd, 2),
                "exit_reason": exit_reason,
                "hold_bars": i - pos["entry_i"],
                "balance": round(_state["balance"], 2),
            }
            _state["trades"].append(trade)

            sk = (pos["date_id"], pos["session"])
            _state["last_cooldown"][sk] = i
            closed.append(pi)

            emoji = "✅" if pnl_pts > 0 else "❌"
            logger.info(f"{emoji} CLOSED: {trade['dir']} @ {pos['entry']:.2f} → {exit_price:.2f} | PnL: {pnl_pts:+.2f} pts (${pnl_usd:+,.2f}) | {exit_reason}")

    for pi in reversed(closed):
        _state["open_pos"].pop(pi)
    if closed:
        _save_state()


# === FLASK APP ===
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="60">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XAUUSD Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Courier New',monospace;background:#0a0a0a;color:#00ff88;padding:20px}
h1{text-align:center;color:#ffcc00;font-size:1.6em;margin-bottom:15px}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:15px}
.c{background:#111;border:1px solid #333;border-radius:6px;padding:12px}
.c h2{color:#ffcc00;font-size:.9em;margin-bottom:8px;border-bottom:1px solid #333;padding-bottom:4px}
.s{display:flex;justify-content:space-between;margin:3px 0;font-size:.9em}
.s .l{color:#888}.s .v{font-weight:bold}
.p{color:#00ff88}.n{color:#ff4444}.u{color:#ffcc00}
table{width:100%;border-collapse:collapse;font-size:.8em}
th{color:#ffcc00;text-align:left;padding:4px;border-bottom:1px solid #333}
td{padding:4px;border-bottom:1px solid #222}
.b{color:#00ff88}.s{color:#ff4444}
.ft{text-align:center;color:#555;font-size:.75em;margin-top:15px}
</style></head><body>
<h1>XAUUSD Paper Trading Bot</h1>
<div class="g">
<div class="c"><h2>Status</h2>
<div class="s"><span class="l">Last Tick:</span><span class="v u">{{last_tick}}</span></div>
<div class="s"><span class="l">Live Price:</span><span class="v u">${{price}}</span></div>
<div class="s"><span class="l">Open Positions:</span><span class="v">{{open_count}}</span></div>
</div>
<div class="c"><h2>Account</h2>
<div class="s"><span class="l">Balance:</span><span class="v p">${{balance}}</span></div>
<div class="s"><span class="l">Peak:</span><span class="v p">${{peak}}</span></div>
<div class="s"><span class="l">Max DD:</span><span class="v n">${{max_dd}}</span></div>
</div>
<div class="c"><h2>Performance</h2>
<div class="s"><span class="l">Trades:</span><span class="v">{{total}}</span></div>
<div class="s"><span class="l">Wins / Losses:</span><span class="v">{{wins}} / {{losses}}</span></div>
<div class="s"><span class="l">Win Rate:</span><span class="v {{wr_cls}}">{{wr}}%</span></div>
</div>
<div class="c"><h2>Strategy</h2>
<div class="s"><span class="l">Signals:</span><span class="v">{{signals}}</span></div>
<div class="s"><span class="l">SL / TP:</span><span class="v">{{sl_tp}}</span></div>
<div class="s"><span class="l">Session:</span><span class="v">08:00-13:00 UTC</span></div>
</div>
</div>
<div class="c"><h2>Last 20 Trades</h2>
<table><tr><th>Time</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL pt</th><th>PnL $</th><th>Reason</th></tr>
{% for t in trades|reverse %}<tr>
<td>{{t.entry_time[:16]}}</td>
<td class="{{'b' if t.dir=='BUY' else 's'}}">{{t.dir}}</td>
<td>{{"%.2f"|format(t.entry)}}</td>
<td>{{"%.2f"|format(t.exit)}}</td>
<td class="{{'b' if t.pnl_pts>0 else 's'}}">{{"%.2f"|format(t.pnl_pts)}}</td>
<td class="{{'b'if t.pnl_usd>0 else 's'}}">${{"{:,.2f}".format(t.pnl_usd)}}</td>
<td>{{t.exit_reason}}</td></tr>{% endfor %}
</table></div>
<div class="c"><h2>Monthly PnL</h2>
<table><tr><th>Month</th><th>Trades</th><th>WR</th><th>PnL</th></tr>
{% for m in monthly %}<tr>
<td>{{m.m}}</td><td>{{m.t}}</td><td>{{m.w}}%</td>
<td class="{{'b' if m.p>0 else 's'}}">${{"{:,.2f}".format(m.p)}}</td></tr>{% endfor %}
</table></div>
<div class="ft">Polls every 5 min | Auto-refresh 60s | Keep-alive: /health</div>
</body></html>
"""


@app.route("/")
def index():
    with _lock:
        wins = sum(1 for t in _state["trades"] if t["pnl_pts"] > 0)
        losses = len(_state["trades"]) - wins
        wr = round(wins / len(_state["trades"]) * 100, 1) if _state["trades"] else 0

        month_map = {}
        for t in _state["trades"]:
            m = t["entry_time"][:7]
            if m not in month_map:
                month_map[m] = {"p": 0, "t": 0, "w": 0}
            month_map[m]["p"] += t["pnl_usd"]
            month_map[m]["t"] += 1
            if t["pnl_pts"] > 0:
                month_map[m]["w"] += 1
        monthly = [{"m": m, "t": v["t"], "w": round(v["w"]/v["t"]*100,1) if v["t"] else 0, "p": v["p"]}
                    for m, v in sorted(month_map.items())]

        price = fetch_live_price()

        return render_template_string(DASHBOARD_HTML,
            last_tick=(_state.get("last_tick") or "Never")[:19],
            price=f"{price:,.2f}" if price else "—",
            open_count=len(_state["open_pos"]),
            balance=f"{_state['balance']:,.2f}",
            peak=f"{_state['peak']:,.2f}",
            max_dd=f"{_state['max_dd']:,.2f}",
            total=len(_state["trades"]),
            wins=wins, losses=losses, wr=wr,
            wr_cls="p" if wr >= 50 else "n",
            signals=" + ".join(cfg.SIGNALS),
            sl_tp=f"{cfg.SL_PTS} / {cfg.TP_PTS}",
            trades=_state["trades"][-20:],
            monthly=monthly,
        )


@app.route("/health")
def health():
    """Keep-alive endpoint for cron-job.org"""
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/tick")
def tick():
    """Manual trigger for paper trading tick"""
    result = run_tick()
    return jsonify(result)


@app.route("/api/status")
def api_status():
    return jsonify({
        "balance": _state["balance"],
        "peak": _state["peak"],
        "max_dd": _state["max_dd"],
        "total_trades": len(_state["trades"]),
        "open_positions": len(_state["open_pos"]),
        "last_tick": _state.get("last_tick"),
    })


@app.route("/api/trades")
def api_trades():
    return jsonify(_state["trades"][-50:])


# === BACKGROUND POLLING THREAD ===
def _poll_loop():
    """Background thread: fetch data and run paper trading every 5 minutes."""
    while True:
        try:
            result = run_tick()
            logger.info(f"Tick: {result.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"Poll error: {e}")
        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    _load_state()

    # Start background polling
    poller = threading.Thread(target=_poll_loop, daemon=True)
    poller.start()
    logger.info("Background poller started (5-min interval)")

    port = int(os.environ.get("PORT", cfg.DASH_PORT))
    logger.info(f"Dashboard: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
