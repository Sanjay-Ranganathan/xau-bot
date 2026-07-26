"""Web dashboard — real-time monitoring of paper trading bot."""
import json
import os
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>XAUUSD Bot — Live Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Courier New',monospace;background:#0a0a0a;color:#00ff88;padding:20px}
h1{text-align:center;color:#ffcc00;font-size:1.8em;margin-bottom:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:15px;margin-bottom:20px}
.card{background:#111;border:1px solid #333;border-radius:8px;padding:15px}
.card h2{color:#ffcc00;font-size:1em;margin-bottom:10px;border-bottom:1px solid #333;padding-bottom:5px}
.stat{display:flex;justify-content:space-between;margin:5px 0}
.stat .label{color:#888}
.stat .value{color:#00ff88;font-weight:bold}
.stat .value.positive{color:#00ff88}
.stat .value.negative{color:#ff4444}
.stat .value.neutral{color:#ffcc00}
table{width:100%;border-collapse:collapse;font-size:0.85em}
th{color:#ffcc00;text-align:left;padding:6px;border-bottom:1px solid #333}
td{padding:6px;border-bottom:1px solid #222}
.buy{color:#00ff88}.sell{color:#ff4444}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
.status-dot.on{background:#00ff88}.status-dot.off{background:#ff4444}
.footer{text-align:center;color:#555;font-size:0.8em;margin-top:20px}
</style>
</head>
<body>
<h1>XAUUSD Bot Dashboard</h1>

<div class="grid">
<div class="card">
<h2><span class="status-dot {{dot_class}}"></span> System Status</h2>
<div class="stat"><span class="label">Bot Status:</span><span class="value {{bot_cls}}">{{status.running|capitalize}}</span></div>
<div class="stat"><span class="label">Feed:</span><span class="value {{feed_cls}}">{{feed_status}}</span></div>
<div class="stat"><span class="label">Latest Price:</span><span class="value neutral">{{latest_price}}</span></div>
<div class="stat"><span class="label">Last Update:</span><span class="value">{{last_update}}</span></div>
</div>

<div class="card">
<h2>Account</h2>
<div class="stat"><span class="label">Balance:</span><span class="value">{{balance}}</span></div>
<div class="stat"><span class="label">Peak:</span><span class="value">{{peak}}</span></div>
<div class="stat"><span class="label">Max Drawdown:</span><span class="value negative">{{max_dd}}</span></div>
<div class="stat"><span class="label">Open Position:</span><span class="value">{{open_pos}}</span></div>
</div>

<div class="card">
<h2>Performance</h2>
<div class="stat"><span class="label">Total Trades:</span><span class="value">{{total_trades}}</span></div>
<div class="stat"><span class="label">Wins / Losses:</span><span class="value">{{wins}} / {{losses}}</span></div>
<div class="stat"><span class="label">Win Rate:</span><span class="value {{wr_cls}}">{{win_rate}}%</span></div>
<div class="stat"><span class="label">Strategy:</span><span class="value neutral">{{strategy}}</span></div>
</div>

<div class="card">
<h2>Configuration</h2>
<div class="stat"><span class="label">Signals:</span><span class="value">{{signals}}</span></div>
<div class="stat"><span class="label">SL / TP:</span><span class="value">{{sl_tp}}</span></div>
<div class="stat"><span class="label">Session:</span><span class="value">{{session}}</span></div>
<div class="stat"><span class="label">Min Agree:</span><span class="value">{{min_agree}}</span></div>
</div>
</div>

<div class="card">
<h2>Recent Trades (Last 20)</h2>
<table>
<tr><th>Time</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL pt</th><th>PnL $</th><th>Reason</th><th>Hold</th></tr>
{% for t in trades|reverse %}
<tr>
<td>{{t.entry_time[:16]}}</td>
<td class="{{'buy' if t.dir=='BUY' else 'sell'}}">{{t.dir}}</td>
<td>{{"%.2f"|format(t.entry)}}</td>
<td>{{"%.2f"|format(t.exit)}}</td>
<td class="{{'buy' if t.pnl_pts>0 else 'sell'}}">{{"%.2f"|format(t.pnl_pts)}}</td>
<td class="{{'buy' if t.pnl_usd>0 else 'sell'}}">${{"{:,.2f}".format(t.pnl_usd)}}</td>
<td>{{t.exit_reason}}</td>
<td>{{t.hold_bars}}</td>
</tr>
{% endfor %}
</table>
</div>

<div class="card">
<h2>Monthly PnL</h2>
<table>
<tr><th>Month</th><th>Trades</th><th>WR</th><th>PnL</th></tr>
{% for m in monthly %}
<tr>
<td>{{m.month}}</td>
<td>{{m.trades}}</td>
<td>{{m.wr}}%</td>
<td class="{{'buy' if m.pnl>0 else 'sell'}}">${{"{:,.2f}".format(m.pnl)}}</td>
</tr>
{% endfor %}
</table>
</div>

<div class="footer">
Last refresh: {{last_update}} | Auto-refresh: 30s
</div>
</body>
</html>
"""


def create_app(paper_trader=None, cfg=None):
    app = Flask(__name__)
    _paper_trader = paper_trader
    _cfg = cfg

    @app.route("/")
    def index():
        status = {}
        trades = []
        monthly = []
        cfg_d = _cfg or {}

        if _paper_trader:
            status = _paper_trader.get_status()
            trades = _paper_trader.get_trades(20)
            # Group trades by month
            month_map = {}
            for t in _paper_trader.get_trades(1000):
                m = t["entry_time"][:7]
                if m not in month_map:
                    month_map[m] = {"pnl": 0, "trades": 0, "wins": 0}
                month_map[m]["pnl"] += t["pnl_usd"]
                month_map[m]["trades"] += 1
                if t["pnl_pts"] > 0:
                    month_map[m]["wins"] += 1
            for m in sorted(month_map.keys()):
                v = month_map[m]
                monthly.append({
                    "month": m,
                    "trades": v["trades"],
                    "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
                    "pnl": v["pnl"],
                })

        feed_status = "Connected" if status.get("connected") else "Disconnected"
        dot_class = "on" if status.get("running") else "off"
        bot_cls = "positive" if status.get("running") else "negative"
        feed_cls = "positive" if status.get("connected") else "negative"
        wr = status.get("win_rate", 0)
        wr_cls = "positive" if wr >= 60 else ("neutral" if wr >= 50 else "negative")

        signals = " + ".join(getattr(_cfg, "SIGNALS", ["h1_trend", "sweep"])) if _cfg else "h1_trend + sweep"
        sl = getattr(_cfg, "SL_PTS", 7) if _cfg else 7
        tp = getattr(_cfg, "TP_PTS", 7) if _cfg else 7
        sess_start = getattr(_cfg, "SESSION_START", 8) if _cfg else 8
        sess_end = getattr(_cfg, "SESSION_END", 13) if _cfg else 13
        min_agree = getattr(_cfg, "MIN_AGREE", 2) if _cfg else 2

        return render_template_string(
            DASHBOARD_HTML,
            status=status,
            dot_class=dot_class,
            bot_cls=bot_cls,
            feed_cls=feed_cls,
            feed_status=feed_status,
            latest_price=f"${status.get('latest_price', 0):,.2f}" if status.get("latest_price") else "—",
            last_update=status.get("last_update", "—")[:19],
            balance=f"${status.get('balance', 0):,.2f}",
            peak=f"${status.get('peak', 0):,.2f}",
            max_dd=f"${status.get('max_dd', 0):,.2f}",
            open_pos=f"{status.get('open_positions', 0)} open" if status.get("open_positions", 0) > 0 else "None",
            total_trades=status.get("total_trades", 0),
            wins=status.get("wins", 0),
            losses=status.get("losses", 0),
            win_rate=wr,
            wr_cls=wr_cls,
            strategy=signals,
            signals=signals,
            sl_tp=f"{sl} / {tp}",
            session=f"{sess_start:02d}:00-{sess_end:02d}:00 UTC",
            min_agree=min_agree,
            trades=trades,
            monthly=monthly,
        )

    @app.route("/api/status")
    def api_status():
        if _paper_trader:
            return jsonify(_paper_trader.get_status())
        return jsonify({"running": False, "message": "No trader configured"})

    @app.route("/api/trades")
    def api_trades():
        last_n = request.args.get("last_n", 50, type=int)
        if _paper_trader:
            return jsonify(_paper_trader.get_trades(last_n))
        return jsonify([])

    @app.route("/api/equity")
    def api_equity():
        if _paper_trader:
            return jsonify(_paper_trader.get_equity_curve())
        return jsonify([])

    @app.route("/api/health")
    def api_health():
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "paper_trader": _paper_trader is not None,
        })

    return app
