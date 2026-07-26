"""MetaTrader 5 data feed — connects to MT5 for live 5-min OHLCV data."""
import time
import logging
from datetime import datetime, timedelta
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class MT5Feed:
    def __init__(self, symbol="XAUUSD", timeframe="M5", server=None, login=None, password=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.server = server
        self.login = login
        self.password = password
        self.connected = False
        self._candle_buffer = deque(maxlen=1000)
        self._last_candle_time = None
        self._mt5 = None

    def connect(self):
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5

            init_args = {}
            if self.server:
                init_args["server"] = self.server
            if self.login:
                init_args["login"] = int(self.login)
            if self.password:
                init_args["password"] = self.password

            if not mt5.initialize(**init_args):
                error = mt5.last_error()
                logger.error(f"MT5 init failed: {error}")
                return False

            info = mt5.account_info()
            if info:
                logger.info(f"MT5 connected: {info.login} @ {info.server}, balance={info.balance}")
            self.connected = True
            return True
        except ImportError:
            logger.error("MetaTrader5 package not installed. Run: pip install MetaTrader5")
            return False
        except Exception as e:
            logger.error(f"MT5 connection error: {e}")
            return False

    def disconnect(self):
        if self._mt5:
            self._mt5.shutdown()
        self.connected = False
        logger.info("MT5 disconnected")

    def _tf_to_mt5(self):
        tf_map = {
            "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
            "M6": 6, "M10": 10, "M12": 12, "M15": 15, "M20": 20,
            "M30": 30, "H1": 16385, "H2": 16386, "H4": 16388,
        }
        return tf_map.get(self.timeframe, 5)

    def get_candles(self, count=1000):
        """Fetch last `count` candles from MT5."""
        if not self.connected or not self._mt5:
            return None

        mt5 = self._mt5
        tf = self._tf_to_mt5()
        rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning("No rates from MT5")
            return None

        candles = []
        for r in rates:
            candles.append({
                "time": datetime.fromtimestamp(r["time"]),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["tick_volume"],
            })
        return candles

    def get_latest_candle(self):
        """Get the latest candle — used for real-time bar updates."""
        candles = self.get_candles(2)
        if candles and len(candles) > 0:
            return candles[-1]
        return None

    def get_ticks(self, count=100):
        """Get last N ticks for tick-level processing."""
        if not self.connected or not self._mt5:
            return None
        mt5 = self._mt5
        ticks = mt5.copy_ticks_from_pos(self.symbol, count, mt5.COPY_TICKS_ALL)
        if ticks is None:
            return None
        result = []
        for t in ticks:
            result.append({
                "time": datetime.fromtimestamp(t["time"]),
                "bid": t["bid"],
                "ask": t["ask"],
                "last": t["last"],
                "volume": t["volume"],
            })
        return result

    def is_connected(self):
        return self.connected

    def health_check(self):
        if not self.connected:
            return False
        try:
            info = self._mt5.account_info()
            return info is not None
        except Exception:
            return False
