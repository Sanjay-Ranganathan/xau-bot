"""Backtester engine — runs strategy on historical data.
Matches the original optimizer behavior: per-session cooldown,
multiple concurrent positions, all-session scanning."""
import sys
import os
import json
from datetime import datetime

import polars as pl
import numpy as np

from .indicators import compute_all
from .signals import generate_signals
from .config import StrategyConfig


def load_data(path):
    print(f"Loading {path}...")
    df = pl.scan_csv(path).collect()
    if df["volume"][0] < 1:
        df = df.with_columns(
            (pl.col("volume") * 1_000_000).cast(pl.Float64).alias("volume")
        )
    return df


def run_backtest(df, cfg=None):
    """Run backtest, return (trades_list, equity_curve, stats_dict)."""
    if cfg is None:
        cfg = StrategyConfig()

    ind = compute_all(df)
    n = ind["n"]
    sl = cfg.SL_PTS
    tp = cfg.TP_PTS
    hold = cfg.HOLD_CANDLES

    trades = []
    open_pos = []  # list of open positions (multiple concurrent)
    # Per-session cooldown: key = (date_id, sess_id) -> last exit candle index
    last_cooldown = {}
    # Per-session daily count: key = (date_id, sess_id) -> count
    daily_sess_count = {}

    balance = cfg.INITIAL_BALANCE
    peak = balance
    max_dd = 0.0
    equity = [balance]

    # Count all unique trading days for trades_per_day
    all_dates = set()
    for i in range(n):
        all_dates.add(str(ind["dates_parsed"][i].date()))
    total_trading_days = len(all_dates)

    # Session breakdown
    session_trades = {0: {"wins": 0, "losses": 0}, 1: {"wins": 0, "losses": 0}, 2: {"wins": 0, "losses": 0}}
    daily_pnl = {}

    for i in range(1, n):
        # --- Check open positions for exits ---
        closed_indices = []
        for pi, pos in enumerate(open_pos):
            hold_bars = i - pos["entry_i"]
            if pos["dir"] == 1:
                hit_sl = ind["l"][i] <= pos["entry"] - sl
                hit_tp = ind["h"][i] >= pos["entry"] + tp
            else:
                hit_sl = ind["h"][i] >= pos["entry"] + sl
                hit_tp = ind["l"][i] <= pos["entry"] - tp

            exit_price = None
            exit_reason = None

            if hit_sl and hit_tp:
                # Use candle direction as tiebreaker (matches original)
                if ind["c"][i] > ind["o"][i]:
                    exit_price = pos["entry"] + (tp if pos["dir"] == 1 else -sl)
                    exit_reason = "tp" if pos["dir"] == 1 else "sl"
                else:
                    exit_price = pos["entry"] + (-tp if pos["dir"] == -1 else -sl)
                    exit_reason = "tp" if pos["dir"] == -1 else "sl"
            elif hit_sl:
                exit_price = pos["entry"] - sl if pos["dir"] == 1 else pos["entry"] + sl
                exit_reason = "sl"
            elif hit_tp:
                exit_price = pos["entry"] + tp if pos["dir"] == 1 else pos["entry"] - tp
                exit_reason = "tp"
            elif hold_bars >= hold:
                exit_price = ind["c"][i]
                exit_reason = "timeout"

            if exit_price is not None:
                pnl_pts = (exit_price - pos["entry"]) * pos["dir"]
                pnl_usd = pnl_pts * 100
                balance += pnl_usd
                is_win = pnl_pts > 0

                trades.append({
                    "entry_time": pos["entry_time"],
                    "exit_time": ind["dates_parsed"][i],
                    "entry_i": pos["entry_i"],
                    "exit_i": i,
                    "dir": "BUY" if pos["dir"] == 1 else "SELL",
                    "entry": pos["entry"],
                    "exit": exit_price,
                    "pnl_pts": round(pnl_pts, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "exit_reason": exit_reason,
                    "hold_bars": hold_bars,
                    "balance": round(balance, 2),
                    "session": pos["session"],
                })

                sess = pos["session"]
                if is_win:
                    session_trades[sess]["wins"] += 1
                else:
                    session_trades[sess]["losses"] += 1

                day_str = str(ind["dates_parsed"][i].date())
                if day_str not in daily_pnl:
                    daily_pnl[day_str] = 0
                daily_pnl[day_str] += pnl_usd

                peak = max(peak, balance)
                dd = peak - balance
                max_dd = max(max_dd, dd)

                # Set per-session cooldown
                sk = (pos["date_id"], pos["session"])
                last_cooldown[sk] = i

                closed_indices.append(pi)

        # Remove closed positions (reverse order to maintain indices)
        for pi in reversed(closed_indices):
            open_pos.pop(pi)

        equity.append(balance)

        # --- Signal generation ---
        sess = ind["sess_id"][i]
        did = ind["date_id"][i]
        sk = (did, sess)

        # Session filter
        if cfg.SESSION_FILTER >= 0 and sess != cfg.SESSION_FILTER:
            continue

        # Hour filter (if not using session filter)
        hr = ind["hour_of_day"][i]
        if cfg.SESSION_FILTER < 0:
            if hr < cfg.SESSION_START or hr >= cfg.SESSION_END:
                continue

        # Per-session cooldown
        last_exit = last_cooldown.get(sk, -999)
        if last_exit > i - cfg.COOLDOWN_CANDLES:
            continue

        # Per-session daily trade limit
        if sk not in daily_sess_count:
            daily_sess_count[sk] = 0
        if daily_sess_count[sk] >= cfg.MAX_DAILY_TRADES:
            continue

        # Max concurrent positions
        if len(open_pos) >= cfg.MAX_CONCURRENT_TRADES:
            continue

        # Don't open duplicate direction in same session if already open
        # (prevents stacking same-direction in one session)
        existing_same_session = any(p["session"] == sess for p in open_pos)
        if existing_same_session:
            continue

        trade_ok, direction, agree = generate_signals(
            ind, i, cfg.SIGNALS, cfg.SWEEP_LEVEL, cfg.MIN_AGREE
        )

        if trade_ok:
            open_pos.append({
                "dir": direction,
                "entry": ind["c"][i],
                "entry_i": i,
                "entry_time": ind["dates_parsed"][i],
                "session": sess,
                "date_id": did,
            })
            daily_sess_count[sk] += 1

    return trades, equity, {
        "session_trades": session_trades,
        "daily_pnl": daily_pnl,
        "max_dd": max_dd,
        "total_trading_days": total_trading_days,
    }


def compute_stats(trades, equity, extra, n_total_candles):
    if not trades:
        return {"total_trades": 0}

    wins = [t for t in trades if t["pnl_pts"] > 0]
    losses = [t for t in trades if t["pnl_pts"] <= 0]
    pnls = [t["pnl_pts"] for t in trades]

    total_days = extra["total_trading_days"]
    wr = len(wins) / len(trades) * 100
    avg_w = np.mean([t["pnl_pts"] for t in wins]) if wins else 0
    avg_l = abs(np.mean([t["pnl_pts"] for t in losses])) if losses else 0.01
    pf = avg_w / avg_l if avg_l > 0 else 999
    total_pnl = sum(pnls)

    # Session breakdown
    st = extra["session_trades"]
    sess_names = ["Asia", "London", "NewYork"]
    sess_info = {}
    for idx, name in enumerate(sess_names):
        w = st[idx]["wins"]
        l_ = st[idx]["losses"]
        t = w + l_
        sess_info[name] = {
            "wins": w,
            "losses": l_,
            "total": t,
            "wr": round(w / t * 100, 1) if t > 0 else 0,
        }

    # Daily stats
    daily = extra["daily_pnl"]
    wins_days = sum(1 for v in daily.values() if v > 0)
    loss_days = sum(1 for v in daily.values() if v < 0)
    flat_days = total_days - wins_days - loss_days

    # Streaks
    streaks = {"max_win": 0, "max_loss": 0, "cur_win": 0, "cur_loss": 0}
    cw = cl = 0
    for t in trades:
        if t["pnl_pts"] > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        streaks["max_win"] = max(streaks["max_win"], cw)
        streaks["max_loss"] = max(streaks["max_loss"], cl)
    streaks["cur_win"] = cw
    streaks["cur_loss"] = cl

    equity_arr = np.array(equity)
    dd_arr = np.maximum.accumulate(equity_arr) - equity_arr
    max_dd_usd = np.max(dd_arr)

    # Monthly PnL
    monthly = {}
    for t in trades:
        m = t["entry_time"].strftime("%Y-%m")
        if m not in monthly:
            monthly[m] = {"pnl": 0, "trades": 0, "wins": 0}
        monthly[m]["pnl"] += t["pnl_usd"]
        monthly[m]["trades"] += 1
        if t["pnl_pts"] > 0:
            monthly[m]["wins"] += 1

    return {
        "total_trades": len(trades),
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "avg_pts": round(np.mean(pnls), 2),
        "total_pnl_pts": round(total_pnl, 2),
        "total_pnl_usd": round(total_pnl * 100, 2),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "max_dd_usd": round(max_dd_usd, 2),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "session_info": sess_info,
        "trading_days": total_days,
        "wins_days": wins_days,
        "loss_days": loss_days,
        "flat_days": flat_days,
        "tp_sl_ratio": StrategyConfig.TP_RATIO,
        "trades_per_day": round(len(trades) / max(total_days, 1), 2),
        "trades_per_session_day": round(
            len(trades) / max(sum(s["total"] for s in sess_info.values()), 1), 2
        ),
        "streaks": streaks,
        "monthly": {
            m: {
                **v,
                "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            }
            for m, v in monthly.items()
        },
    }


def save_results(trades, stats, equity, path="backtest_results.json"):
    data = {
        "config": StrategyConfig.to_dict(),
        "stats": stats,
        "trades": [
            {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in t.items()
            }
            for t in trades
        ],
        "equity": equity,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Results saved to {path}")


def main():
    cfg = StrategyConfig()
    if len(sys.argv) > 1:
        cfg.load(sys.argv[1])

    df = load_data(cfg.CANDLE_FILE)
    print(f"Loaded {len(df)} candles")

    print("Computing indicators...")
    start = datetime.now()
    ind = compute_all(df)
    elapsed = (datetime.now() - start).total_seconds()
    print(f"Indicators done in {elapsed:.1f}s")

    trades, equity, extra = run_backtest(df, cfg)
    stats = compute_stats(trades, equity, extra, len(df))

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Strategy:     {' + '.join(cfg.SIGNALS)}")
    print(f"Min agree:    {cfg.MIN_AGREE}")
    print(f"Session:      {'All' if cfg.SESSION_FILTER < 0 else ['Asia','London','NY'][cfg.SESSION_FILTER]}")
    print(f"SL/TP:        {cfg.SL_PTS} / {cfg.TP_PTS} pt ({cfg.TP_RATIO}:1)")
    print(f"Max positions:{cfg.MAX_CONCURRENT_TRADES}")
    print("-" * 60)
    print(f"Total trades: {stats['total_trades']}")
    print(f"Win rate:     {stats['win_rate']}%")
    print(f"Profit factor:{stats['pf']}")
    print(f"Avg PnL:      {stats['avg_pts']} pts")
    print(f"Total PnL:    {stats['total_pnl_pts']} pts (${stats['total_pnl_usd']:,.2f})")
    print(f"Avg win:      {stats['avg_win']} pts")
    print(f"Avg loss:     {stats['avg_loss']} pts")
    print(f"Max DD:       ${stats['max_dd_usd']:,.2f}")
    print(f"Trades/day:   {stats['trades_per_day']} (over {stats['trading_days']} trading days)")
    print(f"Best trade:   {stats['best_trade']} pts")
    print(f"Worst trade:  {stats['worst_trade']} pts")
    print(f"Streaks:      max win {stats['streaks']['max_win']}, max loss {stats['streaks']['max_loss']}")
    print("-" * 60)

    print("\nSession Breakdown:")
    for name, info in stats["session_info"].items():
        print(
            f"  {name:8s}: {info['total']:4d} trades, {info['wr']:5.1f}% WR ({info['wins']}W {info['losses']}L)"
        )

    print("\nMonthly:")
    print(f"  {'Month':10s}  {'Trades':>6s}  {'WR':>5s}  {'PnL USD':>10s}")
    for m in sorted(stats["monthly"].keys()):
        v = stats["monthly"][m]
        print(f"  {m:10s}  {v['trades']:6d}  {v['wr']:5.1f}%  ${v['pnl']:>10,.2f}")

    print("=" * 60)

    save_results(trades, stats, equity)
    return stats


if __name__ == "__main__":
    main()
