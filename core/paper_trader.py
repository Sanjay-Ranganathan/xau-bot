"""Paper trading engine — live signal generation + position tracking."""
import time
import json
import logging
import threading
from datetime import datetime
from collections import deque

import numpy as np
import polars as pl

from .indicators import compute_all, ema, sma, rsi, atr, adx, bollinger
from .signals import generate_signals
from .config import StrategyConfig
from alerts.notifier import send_alert

logger = logging.getLogger(__name__)

STATE_FILE = "/home/sanjay/xau_bot/data/paper_state.json"


class PaperTrader:
    def __init__(self, feed, cfg=None):
        self.feed = feed
        self.cfg = cfg or StrategyConfig()
        self.running = False
        self._thread = None
        self._candle_buffer = deque(maxlen=500)
        self._ind = None
        self._open_pos = []  # multiple concurrent positions
        self._trades = []
        self._daily_trades = {}
        self._last_cooldown = {}  # per-session cooldown
        self._balance = self.cfg.INITIAL_BALANCE
        self._peak = self._balance
        self._max_dd = 0
        self._last_signal = None
        self._state_file = STATE_FILE
        self._lock = threading.Lock()

        # Pre-load historical candles for indicator computation
        self._load_history()

    def _load_history(self):
        """Load existing 5min data as history buffer for indicator warm-up."""
        try:
            candle_file = self.cfg.CANDLE_FILE
            df = pl.scan_csv(candle_file).tail(500).collect()
            for row in df.iter_rows(named=True):
                self._candle_buffer.append({
                    "time": datetime.fromisoformat(row["time"].replace("T", " ")[:19]),
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                })
            logger.info(f"Loaded {len(self._candle_buffer)} historical candles for warm-up")
        except Exception as e:
            logger.warning(f"Could not load history: {e}")

    def start(self):
        """Start paper trading in background thread."""
        if self.running:
            logger.warning("Already running")
            return

        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Paper trader started")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._save_state()
        logger.info("Paper trader stopped")

    def _run_loop(self):
        """Main loop — check for new candles every 5 seconds."""
        while self.running:
            try:
                self._tick()
                time.sleep(5)
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(30)

    def _tick(self):
        """Single tick — check for new candle, compute indicators, check signals."""
        with self._lock:
            # Get latest candle from feed
            new_candle = self.feed.get_latest_candle()
            if new_candle is None:
                return

            # Check if we already have this candle
            if self._candle_buffer and self._candle_buffer[-1]["time"] == new_candle["time"]:
                self._candle_buffer[-1] = new_candle
            else:
                self._candle_buffer.append(new_candle)
                logger.debug(f"New candle: {new_candle['time']} C={new_candle['close']:.2f}")

            if len(self._candle_buffer) < 100:
                return

            # Build DataFrame for indicator computation
            candles = list(self._candle_buffer)
            df = pl.DataFrame({
                "time": [c["time"].isoformat() for c in candles],
                "open": [c["open"] for c in candles],
                "high": [c["high"] for c in candles],
                "low": [c["low"] for c in candles],
                "close": [c["close"] for c in candles],
                "volume": [c.get("volume", 1.0) for c in candles],
            })

            self._ind = compute_all(df)
            n = self._ind["n"]
            i = n - 1

            # Check exits for all open positions
            self._check_exits(i)

            sess = self._ind["sess_id"][i]
            did = self._ind["date_id"][i]
            sk = (did, sess)

            # Session filter
            if self.cfg.SESSION_FILTER >= 0 and sess != self.cfg.SESSION_FILTER:
                return
            hr = self._ind["hour_of_day"][i]
            if self.cfg.SESSION_FILTER < 0:
                if hr < self.cfg.SESSION_START or hr >= self.cfg.SESSION_END:
                    return

            # Per-session cooldown
            last_exit = self._last_cooldown.get(sk, -999)
            if last_exit > i - self.cfg.COOLDOWN_CANDLES:
                return

            # Daily trade limit per session
            if sk not in self._daily_trades:
                self._daily_trades[sk] = 0
            if self._daily_trades[sk] >= self.cfg.MAX_DAILY_TRADES:
                return

            # Max concurrent positions
            if len(self._open_pos) >= self.cfg.MAX_CONCURRENT_TRADES:
                return

            # Don't stack same session
            if any(p["session"] == sess for p in self._open_pos):
                return

            # Generate signal
            trade_ok, direction, agree = generate_signals(
                self._ind, i, self.cfg.SIGNALS, self.cfg.SWEEP_LEVEL, self.cfg.MIN_AGREE
            )

            if trade_ok:
                entry_price = self._ind["c"][i]
                pos = {
                    "dir": direction,
                    "entry": entry_price,
                    "entry_i": i,
                    "entry_time": self._ind["dates_parsed"][i],
                    "sl": self.cfg.SL_PTS,
                    "tp": self.cfg.TP_PTS,
                    "session": sess,
                    "date_id": did,
                }
                self._open_pos.append(pos)
                self._daily_trades[sk] += 1

                side = "BUY" if direction == 1 else "SELL"
                sl_price = entry_price - self.cfg.SL_PTS if direction == 1 else entry_price + self.cfg.SL_PTS
                tp_price = entry_price + self.cfg.TP_PTS if direction == 1 else entry_price - self.cfg.TP_PTS

                msg = (
                    f"📊 PAPER TRADE OPENED\n"
                    f"Symbol: XAUUSD\n"
                    f"Direction: {side}\n"
                    f"Entry: {entry_price:.2f}\n"
                    f"SL: {sl_price:.2f} ({self.cfg.SL_PTS}pt)\n"
                    f"TP: {tp_price:.2f} ({self.cfg.TP_PTS}pt)\n"
                    f"Time: {self._ind['dates_parsed'][i]}\n"
                    f"Agree: {agree}/{len(self.cfg.SIGNALS)}\n"
                    f"Open positions: {len(self._open_pos)}"
                )
                logger.info(msg)
                send_alert(msg)

    def _check_exits(self, i):
        """Check if any open position should be closed."""
        closed = []
        for pi, pos in enumerate(self._open_pos):
            sl = pos["sl"]
            tp = pos["tp"]
            h = self._ind["h"][i]
            lo = self._ind["l"][i]
            hold_bars = i - pos["entry_i"]

            hit_sl = False
            hit_tp = False

            if pos["dir"] == 1:
                hit_sl = lo <= pos["entry"] - sl
                hit_tp = h >= pos["entry"] + tp
            else:
                hit_sl = h >= pos["entry"] + sl
                hit_tp = lo <= pos["entry"] - tp

            exit_price = None
            exit_reason = None

            if hit_sl and hit_tp:
                if self._ind["c"][i] > self._ind["o"][i]:
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
            elif hold_bars >= self.cfg.HOLD_CANDLES:
                exit_price = self._ind["c"][i]
                exit_reason = "timeout"

            if exit_price is not None:
                pnl_pts = (exit_price - pos["entry"]) * pos["dir"]
                pnl_usd = pnl_pts * 100
                self._balance += pnl_usd
                self._peak = max(self._peak, self._balance)
                dd = self._peak - self._balance
                self._max_dd = max(self._max_dd, dd)

                trade = {
                    "entry_time": pos["entry_time"].isoformat(),
                    "exit_time": self._ind["dates_parsed"][i].isoformat(),
                    "dir": "BUY" if pos["dir"] == 1 else "SELL",
                    "entry": pos["entry"],
                    "exit": exit_price,
                    "pnl_pts": round(pnl_pts, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "exit_reason": exit_reason,
                    "hold_bars": hold_bars,
                    "balance": round(self._balance, 2),
                }
                self._trades.append(trade)

                # Set per-session cooldown
                sk = (pos["date_id"], pos["session"])
                self._last_cooldown[sk] = i

                side = "BUY" if pos["dir"] == 1 else "SELL"
                emoji = "✅" if pnl_pts > 0 else "❌"
                msg = (
                    f"{emoji} PAPER TRADE CLOSED\n"
                    f"Symbol: XAUUSD\n"
                    f"Direction: {side}\n"
                    f"Entry: {pos['entry']:.2f}\n"
                    f"Exit: {exit_price:.2f}\n"
                    f"PnL: {pnl_pts:+.2f} pts (${pnl_usd:+,.2f})\n"
                    f"Reason: {exit_reason.upper()}\n"
                    f"Balance: ${self._balance:,.2f}\n"
                    f"Max DD: ${self._max_dd:,.2f}"
                )
                logger.info(msg)
                send_alert(msg)
                closed.append(pi)

        for pi in reversed(closed):
            self._open_pos.pop(pi)
        if closed:
            self._save_state()

    def _save_state(self):
        state = {
            "balance": self._balance,
            "peak": self._peak,
            "max_dd": self._max_dd,
            "trades": self._trades,
            "open_positions": self._open_pos,
            "daily_trades": self._daily_trades,
            "last_cooldown": self._last_cooldown,
            "last_update": datetime.now().isoformat(),
        }
        try:
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def _load_state(self):
        try:
            with open(self._state_file) as f:
                state = json.load(f)
            self._balance = state.get("balance", self.cfg.INITIAL_BALANCE)
            self._peak = state.get("peak", self._balance)
            self._max_dd = state.get("max_dd", 0)
            self._trades = state.get("trades", [])
            self._open_pos = state.get("open_positions", [])
            self._daily_trades = state.get("daily_trades", {})
            self._last_cooldown = state.get("last_cooldown", {})
            logger.info(f"State loaded: balance=${self._balance:,.2f}, {len(self._trades)} trades, {len(self._open_pos)} open")
        except FileNotFoundError:
            logger.info("No previous state found")
        except Exception as e:
            logger.error(f"State load error: {e}")

    def get_status(self):
        with self._lock:
            wins = [t for t in self._trades if t["pnl_pts"] > 0]
            losses = [t for t in self._trades if t["pnl_pts"] <= 0]
            return {
                "running": self.running,
                "connected": self.feed.is_connected(),
                "balance": self._balance,
                "peak": self._peak,
                "max_dd": self._max_dd,
                "total_trades": len(self._trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(self._trades) * 100, 1) if self._trades else 0,
                "open_positions": len(self._open_pos),
                "open_position": self._open_pos[0] if self._open_pos else None,
                "latest_price": self.feed.get_latest_price(),
                "last_update": datetime.now().isoformat(),
            }

    def get_trades(self, last_n=50):
        with self._lock:
            return self._trades[-last_n:]

    def get_equity_curve(self):
        with self._lock:
            return [{"i": i, "balance": t["balance"]} for i, t in enumerate(self._trades)]
