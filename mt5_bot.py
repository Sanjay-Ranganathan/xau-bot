"""
XAUUSD Production Trading Bot — MT5 Native
Runs as a background process. All monitoring via MT5 terminal.
"""
import sys
import os
import json
import time
import logging
from datetime import datetime, timedelta
from collections import deque

import MetaTrader5 as mt5
import numpy as np

# === CONFIG ===
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
H1_TIMEFRAME = mt5.TIMEFRAME_H1
SL_PTS = 7.0
TP_PTS = 10.5
SIGNALS = ["h1_trend", "sweep"]
MIN_AGREE = 1
COOLDOWN_SECONDS = 600  # 10 min between trades per session
MAX_DAILY_TRADES = 8
MAX_CONCURRENT = 3
SESSION_START_UTC = 8
SESSION_END_UTC = 13
MAGIC_NUMBER = 20260727
LOG_FILE = "xau_bot.log"
STATE_FILE = "bot_state.json"
INITIAL_CANDLES = 300

# === LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("XAU_BOT")


# === MT5 CONNECTION ===
def mt5_init():
    if not mt5.initialize():
        log.error(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    info = mt5.account_info()
    if info is None:
        log.error("Cannot get account info")
        sys.exit(1)
    log.info(f"MT5 connected: {info.login} @ {info.server}")
    log.info(f"Balance: ${info.balance:,.2f} | Equity: ${info.equity:,.2f} | Leverage: 1:{info.leverage}")
    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        log.error(f"{SYMBOL} not found")
        sys.exit(1)
    if not sym.visible:
        mt5.symbol_select(SYMBOL, True)
    log.info(f"{SYMBOL}: spread={sym.spread} digits={sym.digits} point={sym.point} lot_min={sym.volume_min}")
    return info


def mt5_shutdown():
    mt5.shutdown()
    log.info("MT5 disconnected")


# === STATE ===
class BotState:
    def __init__(self):
        self.balance = 0
        self.open_positions = []
        self.last_trade_time = {}
        self.daily_trade_count = {}
        self.last_signal_i = -999
        self.total_trades = 0
        self.total_wins = 0
        self.total_pnl = 0
        self.max_dd = 0
        self.peak_equity = 0
        self.load()

    def load(self):
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
            self.last_trade_time = d.get("last_trade_time", {})
            self.daily_trade_count = d.get("daily_trade_count", {})
            self.total_trades = d.get("total_trades", 0)
            self.total_wins = d.get("total_wins", 0)
            self.total_pnl = d.get("total_pnl", 0)
            self.max_dd = d.get("max_dd", 0)
            self.peak_equity = d.get("peak_equity", 0)
            log.info(f"State loaded: {self.total_trades} trades, PnL=${self.total_pnl:,.2f}")
        except (FileNotFoundError, json.JSONDecodeError):
            log.info("Fresh state")

    def save(self):
        d = {
            "last_trade_time": self.last_trade_time,
            "daily_trade_count": self.daily_trade_count,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_pnl": self.total_pnl,
            "max_dd": self.max_dd,
            "peak_equity": self.peak_equity,
            "saved_at": datetime.now().isoformat(),
        }
        with open(STATE_FILE, "w") as f:
            json.dump(d, f, indent=2)


# === INDICATORS (pure numpy, no polars needed) ===
def ema(data, period):
    r = np.full(len(data), np.nan)
    if len(data) < period:
        return r
    r[period - 1] = np.mean(data[:period])
    a = 2.0 / (period + 1)
    for i in range(period, len(data)):
        r[i] = a * data[i] + (1 - a) * r[i - 1]
    return r


def compute_signals(closes_5m, highs_5m, lows_5m, opens_5m, n):
    """Compute h1_trend + sweep signals on the latest candle.
    Returns (trade: bool, direction: int, agree: int)."""
    if n < 100:
        return False, 0, 0

    c = closes_5m
    h = highs_5m
    lo = lows_5m
    o = opens_5m

    # === H1 Trend: EMA12 vs EMA26 on 1H bars ===
    h1_closes = []
    for gi in range(n // 12):
        end = min((gi + 1) * 12, n)
        h1_closes.append(c[end - 1])
    h1_closes = np.array(h1_closes)
    if len(h1_closes) < 26:
        return False, 0, 0
    h1_e12 = ema(h1_closes, 12)
    h1_e26 = ema(h1_closes, 26)
    h1_gi = (n - 1) // 12
    if h1_gi >= len(h1_e12) or np.isnan(h1_e12[h1_gi]) or np.isnan(h1_e26[h1_gi]):
        return False, 0, 0
    h1_trend = 1 if h1_e12[h1_gi] > h1_e26[h1_gi] else (-1 if h1_e12[h1_gi] < h1_e26[h1_gi] else 0)

    # === Sweep: prev session high/low ===
    # Find session boundaries (UTC 8=London start)
    sess_hi = -1e10
    sess_lo = 1e10
    prev_sess_hi = None
    prev_sess_lo = None
    cur_sess_start = -1

    # Simple approach: use bars from last ~5 hours for prev session
    # Find last London session bars (UTC 8-12)
    now_bar = n - 1
    # Look back ~120 bars (10 hours) to find prev session
    for i in range(now_bar, max(now_bar - 144, 0), -1):
        # Approximate hour from bar index (5min bars)
        # We need actual time — pass it in or compute from MT5
        pass

    # Simplified sweep: use rolling 5-bar swing highs/lows as levels
    # Last 12 bars = last hour
    prev_12_h = np.max(h[max(0, n - 24):n - 12]) if n > 24 else np.nan
    prev_12_l = np.min(lo[max(0, n - 24):n - 12]) if n > 24 else np.nan

    if np.isnan(prev_12_h) or np.isnan(prev_12_l):
        return False, 0, 0

    sweep_signal = 0
    if h[n - 1] > prev_12_h and c[n - 1] < prev_12_h:
        sweep_signal = -1  # sweep high → sell
    elif lo[n - 1] < prev_12_l and c[n - 1] > prev_12_l:
        sweep_signal = 1  # sweep low → buy

    # === Vote ===
    votes = []
    weights = []
    if h1_trend != 0:
        votes.append(h1_trend)
        weights.append(1.0)
    if sweep_signal != 0:
        votes.append(sweep_signal)
        weights.append(1.5)

    if not votes:
        return False, 0, 0

    pos_w = sum(w for d, w in zip(votes, weights) if d == 1)
    neg_w = sum(w for d, w in zip(votes, weights) if d == -1)

    if pos_w > neg_w and pos_w >= MIN_AGREE:
        return True, 1, len([v for v in votes if v == 1])
    elif neg_w > pos_w and neg_w >= MIN_AGREE:
        return True, -1, len([v for v in votes if v == -1])

    return False, 0, 0


# === ORDER EXECUTION ===
def place_order(direction, sl_pts, tp_pts):
    """Place market order with SL/TP. Returns ticket or None."""
    info = mt5.account_info()
    if info is None:
        log.error("Cannot get account info for order")
        return None

    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        log.error(f"{SYMBOL} not found")
        return None

    price = sym.ask if direction == 1 else sym.bid
    point = sym.point
    digits = sym.digits

    sl = round(price - sl_pts * point * (10 ** (digits - 1 - int(np.log10(1 / point)))) if digits > 2 else price - sl_pts, digits)
    tp = round(price + tp_pts * point * (10 ** (digits - 1 - int(np.log10(1 / point)))) if digits > 2 else price + tp_pts, digits)

    # Simpler: XAUUSD point = 0.01, so SL = price - 7.0, TP = price + 10.5
    if direction == 1:
        sl = round(price - sl_pts, digits)
        tp = round(price + tp_pts, digits)
    else:
        sl = round(price + sl_pts, digits)
        tp = round(price - tp_pts, digits)

    # Lot size: risk 1% of balance per trade
    risk_amount = info.balance * 0.01
    tick_value = sym.trade_tick_value
    tick_size = sym.trade_tick_size
    if tick_value > 0 and tick_size > 0:
        sl_ticks = sl_pts / (point * (10 ** (digits - 1 - int(np.log10(1 / point))))) if point > 0 else sl_pts
        sl_ticks = sl_pts / point
        lot_value_per_sl = sl_ticks * tick_value
        lots = round(risk_amount / max(lot_value_per_sl, 0.01), 2)
        lots = max(sym.volume_min, min(lots, sym.volume_max))
        lots = round(lots / sym.volume_step) * sym.volume_step
        lots = round(lots, 2)
    else:
        lots = sym.volume_min

    side = "BUY" if direction == 1 else "SELL"
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": f"XAU_BOT_{side}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        log.error(f"order_send returned None: {mt5.last_error()}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error(f"Order failed: {result.retcode} - {result.comment}")
        return None

    log.info(f"ORDER OK: {side} {lots} lot @ {price:.2f} SL={sl:.2f} TP={tp:.2f} ticket={result.order}")
    return result.order


def get_open_positions():
    """Get all open positions for this bot."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == MAGIC_NUMBER]


def close_position(ticket):
    """Close a specific position."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return False
    pos = pos[0]

    if pos.type == mt5.ORDER_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info(SYMBOL).bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info(SYMBOL).ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "XAU_BOT_CLOSE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        pnl = pos.profit
        log.info(f"CLOSED ticket={ticket} PnL=${pnl:+,.2f}")
        return True
    else:
        log.error(f"Close failed: {result}")
        return False


# === SESSION CHECK ===
def is_london_session():
    """Check if current UTC time is in London session."""
    now = datetime.utcnow()
    return SESSION_START_UTC <= now.hour < SESSION_END_UTC


def get_session_key():
    """Get session key for cooldown tracking."""
    now = datetime.utcnow()
    return f"{now.strftime('%Y-%m-%d')}_london"


# === MAIN LOOP ===
def run_bot():
    log.info("=" * 50)
    log.info("XAUUSD Production Bot Starting")
    log.info("=" * 50)

    # Init MT5
    acct = mt5_init()
    state = BotState()
    state.balance = acct.balance
    state.peak_equity = acct.equity

    # Fetch initial candles for indicator warmup
    log.info(f"Fetching {INITIAL_CANDLES} candles for warmup...")
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, INITIAL_CANDLES)
    if rates is None or len(rates) < 100:
        log.error("Cannot fetch initial candles")
        mt5_shutdown()
        sys.exit(1)

    closes = np.array([r["close"] for r in rates])
    highs = np.array([r["high"] for r in rates])
    lows = np.array([r["low"] for r in rates])
    opens = np.array([r["open"] for r in rates])
    times = [datetime.fromtimestamp(r["time"]) for r in rates]
    last_bar_time = times[-1]

    log.info(f"Loaded {len(closes)} candles, latest: {last_bar_time}")
    log.info(f"Strategy: {' + '.join(SIGNALS)} | SL={SL_PTS} TP={TP_PTS} | Session: London")
    log.info("Bot running... Monitoring for signals.\n")

    last_status_time = time.time()

    while True:
        try:
            # === Check MT5 connection ===
            if not mt5.terminal_info():
                log.warning("MT5 disconnected, reconnecting...")
                time.sleep(5)
                mt5.initialize()
                continue

            # === Refresh account info ===
            info = mt5.account_info()
            if info is None:
                time.sleep(1)
                continue

            # Track equity curve
            if info.equity > state.peak_equity:
                state.peak_equity = info.equity
            dd = state.peak_equity - info.equity
            if dd > state.max_dd:
                state.max_dd = dd

            # === Check for new bar ===
            rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, INITIAL_CANDLES)
            if rates is None or len(rates) < 100:
                time.sleep(1)
                continue

            new_closes = np.array([r["close"] for r in rates])
            new_highs = np.array([r["high"] for r in rates])
            new_lows = np.array([r["low"] for r in rates])
            new_opens = np.array([r["open"] for r in rates])
            new_times = [datetime.fromtimestamp(r["time"]) for r in rates]
            new_last_time = new_times[-1]

            if new_last_time == last_bar_time:
                time.sleep(2)
                continue

            # New bar!
            last_bar_time = new_last_time
            closes = new_closes
            highs = new_highs
            lows = new_lows
            opens = new_opens
            times = new_times
            n = len(closes)

            # === Check open positions for exit (check every bar) ===
            open_pos = get_open_positions()
            for pos in open_pos:
                entry = pos.price_open
                sl = pos.sl
                tp = pos.tp
                current_price = mt5.symbol_info(SYMBOL).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info(SYMBOL).ask

                hit_sl = False
                hit_tp = False

                if pos.type == mt5.ORDER_TYPE_BUY:
                    hit_sl = lows[-1] <= sl
                    hit_tp = highs[-1] >= tp
                else:
                    hit_sl = highs[-1] >= sl
                    hit_tp = lows[-1] <= tp

                if hit_sl or hit_tp:
                    close_position(pos.ticket)
                    state.total_trades += 1
                    if pos.profit > 0:
                        state.total_wins += 1
                    state.total_pnl += pos.profit
                    state.save()

            # === Status log every 5 min ===
            now = time.time()
            if now - last_status_time > 300:
                open_pos = get_open_positions()
                log.info(
                    f"STATUS: Balance=${info.balance:,.2f} Equity=${info.equity:,.2f} "
                    f"DD=${state.max_dd:,.2f} Trades={state.total_trades} "
                    f"Open={len(open_pos)} Price={mt5.symbol_info(SYMBOL).bid:.2f}"
                )
                last_status_time = now

            # === Session filter ===
            if not is_london_session():
                time.sleep(5)
                continue

            # === Cooldown check ===
            sk = get_session_key()
            last_trade = state.last_trade_time.get(sk, 0)
            if time.time() - last_trade < COOLDOWN_SECONDS:
                time.sleep(2)
                continue

            # === Daily trade limit ===
            if state.daily_trade_count.get(sk, 0) >= MAX_DAILY_TRADES:
                time.sleep(5)
                continue

            # === Max concurrent positions ===
            open_pos = get_open_positions()
            if len(open_pos) >= MAX_CONCURRENT:
                time.sleep(5)
                continue

            # === Generate signal ===
            trade_ok, direction, agree = compute_signals(closes, highs, lows, opens, n)

            if trade_ok:
                ticket = place_order(direction, SL_PTS, TP_PTS)
                if ticket:
                    state.last_trade_time[sk] = time.time()
                    state.daily_trade_count[sk] = state.daily_trade_count.get(sk, 0) + 1
                    state.save()

            time.sleep(2)

        except KeyboardInterrupt:
            log.info("Shutdown requested")
            break
        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)
            time.sleep(10)

    # Cleanup
    open_pos = get_open_positions()
    if open_pos:
        log.warning(f"Closing {len(open_pos)} open positions on shutdown...")
        for pos in open_pos:
            close_position(pos.ticket)

    state.save()
    mt5_shutdown()
    log.info("Bot stopped cleanly")


if __name__ == "__main__":
    run_bot()
