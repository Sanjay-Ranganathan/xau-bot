"""Signal generators — each returns (signal: bool, direction: int, weight: float)."""
import numpy as np


def s_h1_trend(ind, i):
    t = ind["h1_trend"][i]
    if t == 0:
        return False, 0, 0
    return True, t, 1.0


def s_sweep(ind, i, level=0):
    if level == 0:
        sh = ind["prev_sess_hi"][i]
        sl_ = ind["prev_sess_lo"][i]
    elif level == 1:
        sh = ind["day_hi_prev"][i]
        sl_ = ind["day_lo_prev"][i]
    else:
        sh = ind["sw5_h"][i]
        sl_ = ind["sw5_l"][i]
    if np.isnan(sh) or np.isnan(sl_):
        return False, 0, 0
    if ind["h"][i] > sh and ind["c"][i] < sh:
        return True, -1, 1.5
    if ind["l"][i] < sl_ and ind["c"][i] > sl_:
        return True, 1, 1.5
    return False, 0, 0


def s_body_flip(ind, i, min_br=0.3):
    if i < 1:
        return False, 0, 0
    if ind["br"][i] < min_br:
        return False, 0, 0
    if not ind["is_bull"][i - 1] and ind["is_bull"][i]:
        return True, 1, 1.0
    if ind["is_bull"][i - 1] and not ind["is_bull"][i]:
        return True, -1, 1.0
    return False, 0, 0


def s_pin_bar(ind, i):
    if ind["rng"][i] < 1.0:
        return False, 0, 0
    body_ = ind["body"][i]
    rng_ = ind["rng"][i]
    if body_ / rng_ > 0.35:
        return False, 0, 0
    lw = min(ind["o"][i], ind["c"][i]) - ind["l"][i]
    uw = ind["h"][i] - max(ind["o"][i], ind["c"][i])
    if lw > 2.0 and lw > uw * 1.5 and ind["c"][i] > ind["o"][i]:
        return True, 1, 1.0
    if uw > 2.0 and uw > lw * 1.5 and ind["c"][i] < ind["o"][i]:
        return True, -1, 1.0
    return False, 0, 0


def s_rsi(ind, i, ob=75, os_=25):
    if ind["rsi14"][i] >= ob and ind["c"][i] < ind["o"][i] and ind["br"][i] >= 0.35:
        return True, -1, 1.0
    if ind["rsi14"][i] <= os_ and ind["c"][i] > ind["o"][i] and ind["br"][i] >= 0.35:
        return True, 1, 1.0
    return False, 0, 0


def s_momentum(ind, i, min_streak=2):
    s = ind["streak"][i]
    if abs(s) < min_streak:
        return False, 0, 0
    return True, (1 if s > 0 else -1), min(abs(s) / 3.0, 2.0)


def s_engulfing(ind, i):
    if ind["engulf_bull"][i]:
        return True, 1, 1.2
    if ind["engulf_bear"][i]:
        return True, -1, 1.2
    return False, 0, 0


def s_vwap(ind, i):
    # Placeholder — VWAP computed elsewhere if needed
    return False, 0, 0


SIGNAL_MAP = {
    "h1_trend": s_h1_trend,
    "sweep": s_sweep,
    "body_flip": s_body_flip,
    "pin_bar": s_pin_bar,
    "rsi": s_rsi,
    "momentum": s_momentum,
    "engulfing": s_engulfing,
}


def generate_signals(ind, i, signal_names, sweep_level=0, min_agree=2):
    """Generate signals and return the final trade direction.
    Returns (trade: bool, direction: int, agree_count: float) or (False, 0, 0)."""
    votes = []
    weights = []
    for name in signal_names:
        if name == "sweep":
            ok, d, w = s_sweep(ind, i, sweep_level)
        elif name == "body_flip":
            ok, d, w = s_body_flip(ind, i)
        elif name == "pin_bar":
            ok, d, w = s_pin_bar(ind, i)
        elif name == "rsi":
            ok, d, w = s_rsi(ind, i)
        elif name == "momentum":
            ok, d, w = s_momentum(ind, i)
        elif name == "engulfing":
            ok, d, w = s_engulfing(ind, i)
        elif name == "h1_trend":
            ok, d, w = s_h1_trend(ind, i)
        else:
            continue
        if ok:
            votes.append(d)
            weights.append(w)

    if not votes:
        return False, 0, 0

    pos_w = sum(w for d, w in zip(votes, weights) if d == 1)
    neg_w = sum(w for d, w in zip(votes, weights) if d == -1)

    if pos_w > neg_w and pos_w >= min_agree:
        agree = sum(1 for d in votes if d == 1)
        return True, 1, agree
    elif neg_w > pos_w and neg_w >= min_agree:
        agree = sum(1 for d in votes if d == -1)
        return True, -1, agree

    return False, 0, 0
