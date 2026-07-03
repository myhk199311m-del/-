"""
indicators.py
Technical analysis engine: 9 indicators + candlestick pattern detection.
Produces a confidence score (0-100) and BUY/SELL/WAIT decision,
mirroring the "Basilisk" style dashboard.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Core indicator calculations (pure pandas/numpy, no external TA lib needed)
# ---------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14):
    """Wilder-smoothed ADX with +DI / -DI."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = true_range(df)
    atr_w = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_w
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_w

    dx = ( (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) ) * 100
    adx_val = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_val, plus_di, minus_di


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


def williams_r(df: pd.DataFrame, period: int = 14):
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    return -100 * (high_max - df["close"]) / (high_max - low_min).replace(0, np.nan)


def cci(df: pd.DataFrame, period: int = 20):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


# ---------------------------------------------------------------------------
# Candlestick pattern detection
# ---------------------------------------------------------------------------

def detect_pattern(df: pd.DataFrame) -> tuple[str, int]:
    """
    Returns (pattern_name, direction) where direction is +1 bullish,
    -1 bearish, 0 neutral/none. Uses the last completed candle.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]

    body = abs(last["close"] - last["open"])
    candle_range = last["high"] - last["low"]
    if candle_range == 0:
        return "None", 0

    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]

    # Pin Bar: small body, long wick on one side (>=2x body)
    if body / candle_range < 0.35:
        if lower_wick > 2 * body and lower_wick > upper_wick:
            return "Pin Bar", 1
        if upper_wick > 2 * body and upper_wick > lower_wick:
            return "Pin Bar", -1

    # Doji
    if body / candle_range < 0.1:
        return "Doji", 0

    # Bullish / Bearish Engulfing
    prev_body = abs(prev["close"] - prev["open"])
    if (last["close"] > last["open"] and prev["close"] < prev["open"]
            and last["close"] >= prev["open"] and last["open"] <= prev["close"]
            and body > prev_body):
        return "Engulfing", 1
    if (last["close"] < last["open"] and prev["close"] > prev["open"]
            and last["open"] >= prev["close"] and last["close"] <= prev["open"]
            and body > prev_body):
        return "Engulfing", -1

    return "None", 0


# ---------------------------------------------------------------------------
# Confidence scoring: 9 indicators + pattern, majority-direction weighting
# ---------------------------------------------------------------------------

def analyze(df: pd.DataFrame) -> dict:
    """
    df must have columns: open, high, low, close (chronological order).
    Returns a dict with decision, score, strength label, pattern, SL/TP.
    """
    close = df["close"]

    ema8, ema21, ema50, ema200 = ema(close, 8), ema(close, 21), ema(close, 50), ema(close, 200)
    rsi_val = rsi(close).iloc[-1]
    macd_line, signal_line = macd(close)
    adx_val, plus_di, minus_di = adx(df)
    upper_bb, mid_bb, lower_bb = bollinger_bands(close)
    stoch_k, stoch_d = stochastic(df)
    wr = williams_r(df)
    cci_val = cci(df)
    pattern_name, pattern_dir = detect_pattern(df)

    price = close.iloc[-1]
    votes = []  # each vote: +1 bullish, -1 bearish, 0 neutral

    # 1. EMA trend stack
    votes.append(1 if (price > ema50.iloc[-1] > ema200.iloc[-1]) else
                 -1 if (price < ema50.iloc[-1] < ema200.iloc[-1]) else 0)

    # 2. EMA fast cross (8/21)
    votes.append(1 if ema8.iloc[-1] > ema21.iloc[-1] else -1)

    # 3. RSI
    votes.append(1 if rsi_val < 35 else -1 if rsi_val > 65 else 0)

    # 4. MACD
    votes.append(1 if macd_line.iloc[-1] > signal_line.iloc[-1] else -1)

    # 5. ADX / DI direction (only counts if trend is strong enough)
    if adx_val.iloc[-1] >= 20:
        votes.append(1 if plus_di.iloc[-1] > minus_di.iloc[-1] else -1)
    else:
        votes.append(0)

    # 6. Bollinger position
    if price <= lower_bb.iloc[-1]:
        votes.append(1)
    elif price >= upper_bb.iloc[-1]:
        votes.append(-1)
    else:
        votes.append(0)

    # 7. Stochastic
    votes.append(1 if stoch_k.iloc[-1] < 20 else -1 if stoch_k.iloc[-1] > 80 else 0)

    # 8. Williams %R
    votes.append(1 if wr.iloc[-1] < -80 else -1 if wr.iloc[-1] > -20 else 0)

    # 9. CCI
    votes.append(1 if cci_val.iloc[-1] < -100 else -1 if cci_val.iloc[-1] > 100 else 0)

    # 10. Candlestick pattern (weighted same as one indicator)
    votes.append(pattern_dir)

    bull = votes.count(1)
    bear = votes.count(-1)
    total = len(votes)

    if bull >= bear:
        direction = "BUY"
        score = round((bull / total) * 100)
    else:
        direction = "SELL"
        score = round((bear / total) * 100)

    if score >= 75:
        strength = "STRONG"
    elif score >= 50:
        strength = "MEDIUM"
    else:
        strength = "WEAK"
        direction = "WAIT"

    atr_val = atr(df).iloc[-1]
    if direction == "BUY":
        sl = price - 1.5 * atr_val
        tp = price + 2.0 * atr_val
    elif direction == "SELL":
        sl = price + 1.5 * atr_val
        tp = price - 2.0 * atr_val
    else:
        sl = tp = None

    return {
        "decision": direction,
        "score": score,
        "strength": strength,
        "pattern": pattern_name,
        "price": price,
        "sl": sl,
        "tp": tp,
        "bull_votes": bull,
        "bear_votes": bear,
        "total_indicators": total,
    }
