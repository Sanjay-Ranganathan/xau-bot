"""All technical indicator calculations — numpy-based for speed."""
import numpy as np


def ema(data, period):
    r = np.full(len(data), np.nan)
    if len(data) < period:
        return r
    r[period - 1] = np.mean(data[:period])
    a = 2.0 / (period + 1)
    for i in range(period, len(data)):
        r[i] = a * data[i] + (1 - a) * r[i - 1]
    return r


def sma(data, period):
    r = np.full(len(data), np.nan)
    cs = np.cumsum(data)
    for i in range(period - 1, len(data)):
        r[i] = (cs[i] - (cs[i - period] if i >= period else 0)) / period
    return r


def rsi(close, period=14):
    n = len(close)
    r = np.full(n, 50.0)
    if n < period + 1:
        return r
    dc = np.diff(close)
    for i in range(period, n):
        chunk = dc[i - period:i]
        g = np.mean(np.maximum(chunk, 0))
        ls = np.mean(np.maximum(-chunk, 0))
        r[i] = 100 - 100 / (1 + g / max(ls, 1e-10))
    return r


def atr(high, low, close, period=14):
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            max(abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])),
        )
    result = np.full(n, 10.0)
    if n >= period:
        result[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def adx(high, low, close, period=14):
    n = len(close)
    di_p = np.zeros(n)
    di_m = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        di_p[i] = up if (up > dn and up > 0) else 0
        di_m[i] = dn if (dn > up and dn > 0) else 0

    result = np.full(n, 25.0)
    if n >= period + 1:
        sma_p = np.mean(di_p[1 : period + 1]) + 1e-10
        sma_m = np.mean(di_m[1 : period + 1]) + 1e-10
        dx = abs(sma_p - sma_m) / (sma_p + sma_m) * 100
        result[period] = dx
        for i in range(period + 1, n):
            sma_p = (sma_p * (period - 1) + di_p[i]) / period
            sma_m = (sma_m * (period - 1) + di_m[i]) / period
            dx = abs(sma_p - sma_m) / (sma_p + sma_m) * 100
            result[i] = (result[i - 1] * (period - 1) + dx) / period
    return result


def bollinger(close, period=20, num_std=2.0):
    n = len(close)
    mid = sma(close, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    width = np.full(n, np.nan)
    for i in range(period - 1, n):
        if not np.isnan(mid[i]):
            std = np.std(close[i - period + 1 : i + 1])
            upper[i] = mid[i] + num_std * std
            lower[i] = mid[i] - num_std * std
            width[i] = (upper[i] - lower[i]) / mid[i] if mid[i] > 0 else 0
    return mid, upper, lower, width


def compute_h1(candles_close, candles_high, candles_low, candles_open, n):
    """Build 1H candles from 5M candles and compute H1 indicators."""
    h1_c = []
    h1_h = []
    h1_l = []
    h1_o = []
    h1_map = {}  # 5M index -> H1 index
    for gi in range(n // 12):
        start = gi * 12
        end = min(start + 12, n)
        h1_o.append(candles_open[start])
        h1_c.append(candles_close[end - 1])
        h1_h.append(max(candles_high[start:end]))
        h1_l.append(min(candles_low[start:end]))
        for j in range(start, end):
            h1_map[j] = gi

    h1_c = np.array(h1_c)
    n_h1 = len(h1_c)
    h1_e12 = ema(h1_c, 12)
    h1_e26 = ema(h1_c, 26)

    h1_trend = np.zeros(n, dtype=np.int32)
    for i in range(n):
        gi = h1_map.get(i, 0)
        if gi < n_h1:
            if not np.isnan(h1_e12[gi]) and not np.isnan(h1_e26[gi]):
                if h1_e12[gi] > h1_e26[gi]:
                    h1_trend[i] = 1
                elif h1_e12[gi] < h1_e26[gi]:
                    h1_trend[i] = -1
    return h1_trend, h1_map


def compute_all(df):
    """Compute ALL indicators from a Polars DataFrame with OHLCV columns.
    Returns a dict of numpy arrays ready for signal generation."""
    n = len(df)
    c = df["close"].to_numpy().astype(np.float64)
    h = df["high"].to_numpy().astype(np.float64)
    lo = df["low"].to_numpy().astype(np.float64)
    o = df["open"].to_numpy().astype(np.float64)
    v = df["volume"].to_numpy().astype(np.float64)

    body = np.abs(c - o)
    rng = h - lo
    br = np.where(rng > 1e-8, body / rng, 0.5)
    is_bull = c > o

    atr14 = atr(h, lo, c, 14)
    e12 = ema(c, 12)
    e26 = ema(c, 26)
    e21 = ema(c, 21)
    e50 = ema(c, 50)
    rsi14 = rsi(c, 14)
    adx14 = adx(h, lo, c, 14)
    bb_mid, bb_upper, bb_lower, bb_width = bollinger(c, 20, 2.0)

    # Volume average
    vs = np.full(n, 1.0)
    cs_v = np.cumsum(v)
    for i in range(19, n):
        vs[i] = (cs_v[i] - (cs_v[i - 20] if i >= 20 else 0)) / min(i + 1, 20)

    # Session / date parsing
    from datetime import datetime

    sess_id = np.zeros(n, dtype=np.int32)
    date_id = np.zeros(n, dtype=np.int32)
    hour_of_day = np.zeros(n, dtype=np.int32)
    dates_parsed = []
    t_list = df["time"].to_list()
    for i in range(n):
        ti = t_list[i]
        dt_obj = (
            datetime.fromisoformat(ti[:19].replace("T", " "))
            if isinstance(ti, str)
            else ti
        )
        dates_parsed.append(dt_obj)
        hr = dt_obj.hour
        hour_of_day[i] = hr
        sess_id[i] = 0 if hr < 8 else (1 if hr < 13 else 2)
    date_counter = 0
    date_map = {}
    for i in range(n):
        dstr = str(dates_parsed[i].date())
        if dstr not in date_map:
            date_map[dstr] = date_counter
            date_counter += 1
        date_id[i] = date_map[dstr]

    # Session groups
    sd_map = {}
    for i in range(n):
        key = (date_id[i], sess_id[i])
        if key not in sd_map:
            sd_map[key] = []
        sd_map[key].append(i)
    sorted_keys = sorted(sd_map.keys())

    # Running session hi/lo (no look-ahead)
    run_sess_hi = np.full(n, np.nan)
    run_sess_lo = np.full(n, np.nan)
    for key in sorted_keys:
        indices = sd_map[key]
        cur_hi = -1e10
        cur_lo = 1e10
        for j in indices:
            if h[j] > cur_hi:
                cur_hi = h[j]
            if lo[j] < cur_lo:
                cur_lo = lo[j]
            run_sess_hi[j] = cur_hi
            run_sess_lo[j] = cur_lo

    # Prev session hi/lo
    sd_data = {}
    for key in sorted_keys:
        indices = sd_map[key]
        sd_data[key] = {"hi": max(h[j] for j in indices), "lo": min(lo[j] for j in indices)}
    prev_sess_hi = np.full(n, np.nan)
    prev_sess_lo = np.full(n, np.nan)
    for ki, key in enumerate(sorted_keys):
        if ki > 0:
            pd_ = sd_data[sorted_keys[ki - 1]]
            for j in sd_map[key]:
                prev_sess_hi[j] = pd_["hi"]
                prev_sess_lo[j] = pd_["lo"]

    # Previous day hi/lo
    day_hi_prev = np.full(n, np.nan)
    day_lo_prev = np.full(n, np.nan)
    date_sums = {}
    for i in range(n):
        did = date_id[i]
        if did not in date_sums:
            date_sums[did] = [-1e10, 1e10]
        date_sums[did][0] = max(date_sums[did][0], h[i])
        date_sums[did][1] = min(date_sums[did][1], lo[i])
    sd_list = sorted(date_sums.keys())
    for ki, did in enumerate(sd_list):
        if ki > 0:
            for j in range(n):
                if date_id[j] == did:
                    day_hi_prev[j] = date_sums[sd_list[ki - 1]][0]
                    day_lo_prev[j] = date_sums[sd_list[ki - 1]][1]

    # Swing 5
    sw5_h = np.full(n, np.nan)
    sw5_l = np.full(n, np.nan)
    for i in range(5, n - 5):
        if h[i] == np.max(h[i - 5 : i + 6]):
            sw5_h[i] = h[i]
        if lo[i] == np.min(lo[i - 5 : i + 6]):
            sw5_l[i] = lo[i]

    # Streak
    streak = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if is_bull[i]:
            streak[i] = max(streak[i - 1], 0) + 1
        else:
            streak[i] = min(streak[i - 1], 0) - 1

    # Candle patterns
    engulf_bull = np.zeros(n, dtype=bool)
    engulf_bear = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if is_bull[i] and not is_bull[i - 1]:
            if o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1]:
                engulf_bull[i] = True
        if not is_bull[i] and is_bull[i - 1]:
            if o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1]:
                engulf_bear[i] = True

    # H1 trend
    h1_trend, h1_map = compute_h1(c, h, lo, o, n)

    return {
        "n": n,
        "c": c,
        "h": h,
        "l": lo,
        "o": o,
        "v": v,
        "body": body,
        "rng": rng,
        "br": br,
        "is_bull": is_bull,
        "e12": e12,
        "e26": e26,
        "e21": e21,
        "e50": e50,
        "atr14": atr14,
        "rsi14": rsi14,
        "adx14": adx14,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "vs": vs,
        "sess_id": sess_id,
        "date_id": date_id,
        "hour_of_day": hour_of_day,
        "run_sess_hi": run_sess_hi,
        "run_sess_lo": run_sess_lo,
        "prev_sess_hi": prev_sess_hi,
        "prev_sess_lo": prev_sess_lo,
        "day_hi_prev": day_hi_prev,
        "day_lo_prev": day_lo_prev,
        "sw5_h": sw5_h,
        "sw5_l": sw5_l,
        "streak": streak,
        "engulf_bull": engulf_bull,
        "engulf_bear": engulf_bear,
        "h1_trend": h1_trend,
        "dates_parsed": dates_parsed,
        "t_list": t_list,
        "_sd_map": sd_map,
        "_sorted_keys": sorted_keys,
    }
