"""Strategy configuration — single source of truth for all parameters."""
import json
import os


class StrategyConfig:
    # === STRATEGY ===
    SIGNALS = ["h1_trend", "sweep"]
    MIN_AGREE = 1

    # === RISK ===
    SL_PTS = 7.0
    TP_RATIO = 1.5
    TP_PTS = 10.5  # SL_PTS * TP_RATIO
    MAX_DAILY_TRADES = 8
    MAX_CONCURRENT_TRADES = 3   # how many positions open at once
    COOLDOWN_CANDLES = 2        # per-session cooldown

    # === SESSION (UTC) ===
    # -1 = all sessions, 0=Asia(0-7), 1=London(8-12), 2=NY(13-22)
    SESSION_FILTER = 1          # London only
    SESSION_START = 8
    SESSION_END = 13

    # === TIMEFRAME ===
    TF_MINUTES = 5
    H1_CANDLES_PER = 12  # 12 x 5min = 1 hour
    HOLD_CANDLES = 20    # max hold = 100 min

    # === EMA ===
    EMA_FAST = 12
    EMA_SLOW = 26

    # === SWEEP ===
    SWEEP_LEVEL = 0  # 0=prev_session, 1=prev_day, 2=5swing

    # === TRAILING (optional) ===
    TRAIL_ENABLED = False
    TRIGGER_DIST = 7.0
    TRAIL_DIST = 5.0

    # === DATA ===
    CANDLE_FILE = "/home/sanjay/xauusd_5min_1yr.csv"

    # === PAPER TRADING ===
    MODE = "paper"  # "backtest", "paper", "live"
    INITIAL_BALANCE = 10000.0
    LOT_SIZE = 0.01

    # === TELEGRAM ===
    TG_ENABLED = False
    TG_BOT_TOKEN = ""
    TG_CHAT_ID = ""

    # === DASHBOARD ===
    DASH_HOST = "0.0.0.0"
    DASH_PORT = 8080

    @classmethod
    def to_dict(cls):
        return {k: v for k, v in vars(cls).items() if not k.startswith("_") and not callable(v)}

    @classmethod
    def save(cls, path="config.json"):
        with open(path, "w") as f:
            json.dump(cls.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path="config.json"):
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(cls, k):
                    setattr(cls, k, v)
