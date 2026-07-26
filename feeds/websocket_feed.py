"""WebSocket data feed — connects to free live price feeds via WebSocket."""
import time
import json
import logging
import threading
from datetime import datetime
from collections import deque

import websocket

logger = logging.getLogger(__name__)

# Free WebSocket feeds for XAUUSD
FEEDS = {
    "twelvedata": {
        "url": "wss://ws.twelvedata.com/v1/quotes/price?symbol=XAU/USD&apikey=demo",
        "parse": lambda msg: {
            "time": datetime.now(),
            "bid": float(msg.get("bid", 0)),
            "ask": float(msg.get("ask", 0)),
            "last": float(msg.get("last_price", 0)),
            "volume": 0,
        },
    },
    "finnhub": {
        "url": "wss://ws.finnhub.io?token=demo",
        "subscribe_msg": {"type": "subscribe", "symbol": "OANDA:XAU_USD"},
        "parse": lambda msg: {
            "time": datetime.fromtimestamp(msg.get("t", 0) / 1000),
            "bid": float(msg.get("bp", 0)),
            "ask": float(msg.get("ap", 0)),
            "last": float(msg.get("p", 0)),
            "volume": int(msg.get("v", 0)),
        },
    },
    "polytrade": {
        "url": "wss://socket.polytrade.io",
        "subscribe_msg": {"type": "subscribe", "instrument": "XAU-USD"},
        "parse": lambda msg: {
            "time": datetime.now(),
            "bid": float(msg.get("bid", 0)),
            "ask": float(msg.get("ask", 0)),
            "last": float(msg.get("last", 0)),
            "volume": 0,
        },
    },
}


class WebSocketFeed:
    def __init__(self, feed_name="twelvedata", max_retries=5):
        self.feed_name = feed_name
        self.max_retries = max_retries
        self.connected = False
        self._ws = None
        self._thread = None
        self._ticks = deque(maxlen=10000)
        self._candles_5m = deque(maxlen=2000)
        self._current_candle = None
        self._callbacks = []
        self._running = False
        self._last_price = 0
        self._retry_count = 0

        if feed_name not in FEEDS:
            raise ValueError(f"Unknown feed: {feed_name}. Available: {list(FEEDS.keys())}")
        self._feed_config = FEEDS[feed_name]

    def on_tick(self, callback):
        self._callbacks.append(callback)

    def connect(self):
        try:
            feed_cfg = self._feed_config
            logger.info(f"Connecting to {self.feed_name}: {feed_cfg['url'][:50]}...")

            self._ws = websocket.WebSocketApp(
                feed_cfg["url"],
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._running = True
            self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
            self._thread.start()

            time.sleep(2)
            if self.connected:
                return True

            # If not connected after 2s, try fallback feed
            if self.feed_name != "finnhub":
                logger.info("Primary feed slow, trying fallback...")
                self.disconnect()
                return self.connect_fallback()
            return self.connected
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    def connect_fallback(self):
        """Try alternative feeds if primary fails."""
        for name, cfg in FEEDS.items():
            if name == self.feed_name:
                continue
            logger.info(f"Trying fallback feed: {name}")
            self.feed_name = name
            self._feed_config = cfg
            try:
                self._ws = websocket.WebSocketApp(
                    cfg["url"],
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._running = True
                self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
                self._thread.start()
                time.sleep(3)
                if self.connected:
                    return True
            except Exception as e:
                logger.warning(f"Fallback {name} failed: {e}")
                continue
        return False

    def _on_open(self, ws):
        self.connected = True
        self._retry_count = 0
        logger.info(f"Connected to {self.feed_name}")
        # Send subscribe message if needed
        if "subscribe_msg" in self._feed_config:
            ws.send(json.dumps(self._feed_config["subscribe_msg"]))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if isinstance(data, dict) and not data.get("type"):
                # Skip subscription confirmations
                return
            if isinstance(data, dict) and data.get("type") in ("ping", "heartbeat"):
                return

            tick = self._feed_config["parse"](data)
            if tick["last"] > 0:
                self._last_price = tick["last"]
                self._ticks.append(tick)
                self._process_candle(tick)

                for cb in self._callbacks:
                    try:
                        cb(tick)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
        except Exception as e:
            logger.debug(f"Parse skip: {e}")

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        logger.warning(f"WebSocket closed: {close_status_code} {close_msg}")
        if self._running and self._retry_count < self.max_retries:
            self._retry_count += 1
            logger.info(f"Reconnecting (attempt {self._retry_count})...")
            time.sleep(5 * self._retry_count)
            self.connect()

    def _process_candle(self, tick):
        """Aggregate ticks into 5-min candles."""
        t = tick["time"]
        candle_ts = t.replace(
            minute=(t.minute // 5) * 5,
            second=0, microsecond=0,
        )

        if self._current_candle is None or self._current_candle["time"] != candle_ts:
            if self._current_candle is not None:
                self._candles_5m.append(self._current_candle)
            self._current_candle = {
                "time": candle_ts,
                "open": tick["last"],
                "high": tick["last"],
                "low": tick["last"],
                "close": tick["last"],
                "volume": tick.get("volume", 1),
            }
        else:
            c = self._current_candle
            c["high"] = max(c["high"], tick["last"])
            c["low"] = min(c["low"], tick["last"])
            c["close"] = tick["last"]
            c["volume"] += tick.get("volume", 1)

    def get_candles(self, count=100):
        """Get recent 5-min candles (completed + current)."""
        result = list(self._candles_5m)
        if self._current_candle:
            result.append(self._current_candle)
        return result[-count:]

    def get_latest_price(self):
        return self._last_price

    def get_latest_candle(self):
        if self._current_candle:
            return self._current_candle
        if self._candles_5m:
            return self._candles_5m[-1]
        return None

    def get_ticks(self, count=100):
        return list(self._ticks)[-count:]

    def is_connected(self):
        return self.connected

    def health_check(self):
        if not self.connected:
            return False
        if self._last_price == 0:
            return False
        if self._ticks:
            last_tick_time = self._ticks[-1]["time"]
            age = (datetime.now() - last_tick_time).total_seconds()
            return age < 60
        return False

    def disconnect(self):
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=5)
        self.connected = False
        logger.info(f"Disconnected from {self.feed_name}")
