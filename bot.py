import os
import time
import json
import threading
import logging
import csv
from datetime import datetime
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ==============================================
# ⚙️ الإعدادات العامة
# ==============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 85

# إدارة مخاطر إضافية
MAX_DAILY_LOSS = 5.0
MAX_CONSECUTIVE_LOSSES = 3
TRADING_ENABLED = True
SPREAD = 0.0002

# قائمة الأزواج
SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "CAD/CHF": "CADCHF=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "GBP/AUD": "GBPAUD=X", "XAU/USD": "GC=F",
}

# 🕒 الأطر الزمنية المعدلة: حذف 1د وإضافة 2د، 3د، 10د
TIMEFRAMES = {
    "2د":   {"interval": "2m",  "period": "2d",  "label": "2 دقائق",  "bars": 60, "exp": "02:00"},
    "3د":   {"interval": "3m",  "period": "3d",  "label": "3 دقائق",  "bars": 60, "exp": "03:00"},
    "5د":   {"interval": "5m",  "period": "5d",  "label": "5 دقائق",  "bars": 60, "exp": "05:00"},
    "10د":  {"interval": "10m", "period": "7d",  "label": "10 دقيقة", "bars": 70, "exp": "10:00"},
    "15د":  {"interval": "15m", "period": "10d", "label": "15 دقيقة", "bars": 80, "exp": "15:00"},
    "30د":  {"interval": "30m", "period": "15d", "label": "30 دقيقة", "bars": 80, "exp": "30:00"},
    "1س":   {"interval": "60m", "period": "30d", "label": "ساعة",     "bars": 100, "exp": "01:00:00"},
}

# ⚙️ إعدادات التحليل والأوزان المحسنة
MAIN_TF        = "5د"
CONFIRM_TFS    = ["10د", "15د"]  # توسيع أطر التأكيد لزيادة الدقة
MIN_CONFIRM    = 1
MIN_SCORE      = 7               # رفع الحد قليلاً لتصفية الإشارات الضعيفة
ADX_THRESHOLD  = 25
BB_WIDTH_THRESHOLD = 0.0012       # فلتر ضيق الأسعار
SUPERTREND_MULT = 1.5
SUPERTREND_PERIOD = 10
CCI_THRESHOLD = 100
MOMENTUM_PERIOD = 10
COOLDOWN       = 240
FETCH_DELAY    = 1.8
MAX_FETCH_RETRIES = 3

# أوزان المؤشرات (تشمل المؤشرات الجديدة)
WEIGHTS = {
    "adx": 3, "ema": 2, "supertrend": 3, "ichimoku": 3, "parabolic": 2,
    "rsi": 2, "macd": 3, "stoch": 2, "cci": 2, "momentum": 2,
    "bollinger": 2, "keltner": 2, "donchian": 2, "volatility": 2,
    "pivot": 2, "candles": 3, "heikin": 2
}

LOG_FILE = "signals_log.csv"
RESULTS_FILE = "results_tracking.csv"
last_sig = {}
consecutive_losses = 0
daily_pnl = 0.0
lock = threading.Lock()

# 📅 فلتر الأوقات والأخبار
AVOID_HOURS = [0, 1, 13, 14, 15, 16]
AVOID_DAYS = [5]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
app = Flask(__name__)

# ==============================================
# 📡 اتصال تيليجرام
# ==============================================
def tg(method, data):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        return requests.post(url, json=data, timeout=20).json()
    except Exception as e:
        logging.warning(f"Telegram Error: {e}")
        return {"ok": False}

def send(cid, txt, kb=None):
    p = {"chat_id": cid, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb: p["reply_markup"] = kb
    for _ in range(3):
        res = tg("sendMessage", p)
        if res.get("ok"): return res
        time.sleep(1)
    return {"ok": False}

def edit(cid, mid, txt, kb=None):
    p = {"chat_id": cid, "message_id": mid, "text": txt, "parse_mode": "HTML"}
    if kb: p["reply_markup"] = kb
    return tg("editMessageText", p)

def answer(cbid):
    tg("answerCallbackQuery", {"callback_query_id": cbid})

# ==============================================
# 🎛️ واجهة الأزرار
# ==============================================
def otc(name): return f"{name} OTC"

def kb_main():
    return {"inline_keyboard": [
        [{"text": "⚡ فحص الكل", "callback_data": "SCANALL"}],
        [{"text": "📊 اختر زوج", "callback_data": "PAIRS"}, {"text": "🏆 أقوى إشارة", "callback_data": "BEST"}],
        [{"text": "📈 تقرير الأداء", "callback_data": "REPORT"}, {"text": "💰 إدارة المخاطر", "callback_data": "RISK"}],
        [{"text": "🔙 اختبار تاريخي", "callback_data": "BACKTEST"}, {"text": "❓ مساعدة", "callback_data": "HELP"}]
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows = []
    for i in range(0, len(names), 2):
        rows.append([{"text": f"● {p} OTC", "callback_data": f"P:{p}"} for p in names[i:i+2]])
    rows.append([{"text": "◄ رجوع", "callback_data": "MAIN"}])
    return {"inline_keyboard": rows}

def kb_tf(pair):
    tfs = list(TIMEFRAMES.items())
    rows = []
    for i in range(0, len(tfs), 3):
        rows.append([{"text": v["exp"], "callback_data": f"T:{pair}:{k}"} for k, v in tfs[i:i+3]])
    rows.append([{"text": "◄ رجوع", "callback_data": "PAIRS"}])
    return {"inline_keyboard": rows}

def kb_result():
    return {"inline_keyboard": [[{"text": "⚡ فحص مجدداً", "callback_data": "SCANALL"}, {"text": "🏠 القائمة", "callback_data": "MAIN"}]]}

# ==============================================
# 📊 جلب البيانات المحسن
# ==============================================
def fetch(ticker, interval, period, min_bars=60):
    retries = 0
    while retries < MAX_FETCH_RETRIES:
        try:
            df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False, timeout=15)
            if df.empty:
                retries += 1
                time.sleep(1)
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            req_cols = ["open", "high", "low", "close"]
            if not set(req_cols).issubset(df.columns):
                retries += 1
                time.sleep(1)
                continue
            df = df[req_cols].dropna()
            df = df[df["high"] != df["low"]]
            if len(df) < min_bars:
                retries += 1
                time.sleep(1)
                continue
            df["volume"] = 0
            return df.copy()
        except Exception as e:
            logging.warning(f"Fetch attempt {retries+1} failed for {ticker}: {e}")
            retries += 1
            time.sleep(2)
    logging.error(f"Failed to fetch {ticker} after {MAX_FETCH_RETRIES} attempts")
    return pd.DataFrame()

# ==============================================
# 📊 المؤشرات الفنية الاحترافية
# ==============================================
def EMA(s, p): return s.ewm(span=p, adjust=False).mean()
def SMA(s, p): return s.rolling(p).mean()

def RSI(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/(l + 1e-9))

def MACD(s, fast=12, slow=26, signal=9):
    f = s.ewm(span=fast, adjust=False).mean()
    sl = s.ewm(span=slow, adjust=False).mean()
    ln = f - sl
    return ln, ln.ewm(span=signal, adjust=False).mean()

def BB(s, p=20):
    m = s.rolling(p).mean()
    std = s.rolling(p).std(ddof=0)
    upper = m + 2*std
    lower = m - 2*std
    width = (upper - lower) / (m + 1e-9)
    return upper, m, lower, width

def ATR_Wilder(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = pd.Series(index=tr.index, dtype="float64")
    atr.iloc[p-1] = tr.iloc[:p].mean()
    for i in range(p, len(tr)):
        atr.iloc[i] = (atr.iloc[i-1] * (p-1) + tr.iloc[i]) / p
    return atr

def ADX_Wilder(h, l, c, p=14):
    up = h.diff()
    down = -l.diff()
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)

    tr_s = pd.Series(tr, index=h.index)
    pdm_s = pd.Series(pdm, index=h.index)
    ndm_s = pd.Series(ndm, index=h.index)

    tr_roll = tr_s.rolling(p).sum()
    pdm_roll = pdm_s.rolling(p).sum()
    ndm_roll = ndm_s.rolling(p).sum()

    for i in range(p, len(tr_roll)):
        tr_roll.iloc[i] = tr_roll.iloc[i-1] - tr_roll.iloc[i-1]/p + tr_s.iloc[i]
        pdm_roll.iloc[i] = pdm_roll.iloc[i-1] - pdm_roll.iloc[i-1]/p + pdm_s.iloc[i]
        ndm_roll.iloc[i] = ndm_roll.iloc[i-1] - ndm_roll.iloc[i-1]/p + ndm_s.iloc[i]

    atr = tr_roll.replace(0, 1e-9)
    pdi = 100 * pdm_roll / atr
    ndi = 100 * ndm_roll / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)

    adx = pd.Series(index=dx.index, dtype="float64")
    adx.iloc[p*2 - 1] = dx.iloc[p:p*2].mean()
    for i in range(p*2, len(dx)):
        adx.iloc[i] = (adx.iloc[i-1] * (p-1) + dx.iloc[i]) / p

    return adx, pdi, ndi

def STOCH(h,l,c,k=14,d=3):
    ll = l.rolling(k, min_periods=1).min()
    hh = h.rolling(k, min_periods=1).max()
    return 100 * (c - ll) / (hh - ll + 1e-9)

def SuperTrend(h,l,c,p=10,m=1.5):
    atr = ATR_Wilder(h,l,c,p)
    hl2 = (h + l) / 2
    upper_band = hl2 + m * atr
    lower_band = hl2 - m * atr
    st = pd.Series(index=c.index, dtype="float64")
    trend = pd.Series(0, index=c.index)

    for i in range(1, len(c)):
        if c.iloc[i-1] <= upper_band.iloc[i-1]:
            upper_band.iloc[i] = min(upper_band.iloc[i], upper_band.iloc[i-1])
        if c.iloc[i-1] >= lower_band.iloc[i-1]:
            lower_band.iloc[i] = max(lower_band.iloc[i], lower_band.iloc[i-1])

        if c.iloc[i] > upper_band.iloc[i]:
            trend.iloc[i] = 1
            st.iloc[i] = lower_band.iloc[i]
        elif c.iloc[i] < lower_band.iloc[i]:
            trend.iloc[i] = -1
            st.iloc[i] = upper_band.iloc[i]
        else:
            trend.iloc[i] = trend.iloc[i-1]
            st.iloc[i] = st.iloc[i-1]

    return st, trend

def Ichimoku(h,l,c):
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun)/2).shift(26)
    senkou_b = ((h.rolling(52).max() + l.rolling(52).min())/2).shift(26)
    chikou = c.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou

def CCI(h,l,c,p=20):
    tp = (h + l + c)/3
    ma = tp.rolling(p).mean()
    md = tp.rolling(p).apply(lambda x: np.mean(np.abs(x - np.mean(x))))
    return (tp - ma) / (0.015 * (md + 1e-9))

def Parabolic_SAR(h,l, step=0.02, max_step=0.2):
    psar = pd.Series(index=h.index, dtype="float64")
    trend = pd.Series(1, index=h.index)
    ep = l.iloc[0]
    af = step
    psar.iloc[0] = ep

    for i in range(1, len(h)):
        if trend.iloc[i-1] == 1:
            psar.iloc[i] = psar.iloc[i-1] + af * (ep - psar.iloc[i-1])
            if l.iloc[i] < psar.iloc[i]:
                trend.iloc[i] = -1
                psar.iloc[i] = ep
                ep = h.iloc[i]
                af = step
            else:
                trend.iloc[i] = 1
                if h.iloc[i] > ep:
                    ep = h.iloc[i]
                    af = min(af + step, max_step)
        else:
            psar.iloc[i] = psar.iloc[i-1] + af * (ep - psar.iloc[i-1])
            if h.iloc[i] > psar.iloc[i]:
                trend.iloc[i] = 1
                psar.iloc[i] = ep
                ep = l.iloc[i]
                af = step
            else:
                trend.iloc[i] = -1
                if l.iloc[i] < ep:
                    ep = l.iloc[i]
                    af = min(af + step, max_step)
    return psar, trend

def Donchian(h,l,p=20):
    upper = h.rolling(p).max()
    lower = l.rolling(p).min()
    mid = (upper + lower)/2
    return upper, mid, lower

def Keltner(h,l,c,p=20,m=1.5):
    ma = c.rolling(p).mean()
    atr = ATR_Wilder(h,l,c,p)
    return ma + m*atr, ma, ma - m*atr

def Heikin_Ashi(df):
    ha = df.copy()
    ha["close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha["open"] = ((df["open"].shift(1) + df["close"].shift(1)) / 2).fillna((df["open"] + df["close"])/2)
    ha["high"] = ha[["open", "close", "high"]].max(axis=1)
    ha["low"] = ha[["open", "close", "low"]].min(axis=1)
    return ha

def MOMENTUM(s, p=10):
    return s - s.shift(p)

def VOLATILITY(s, p=20):
    return s.pct_change().rolling(p).std() * np.sqrt(252)

def Daily_Weekly_Pivot(df):
    d_h = float(df["high"].tail(48).max())
    d_l = float(df["low"].tail(48).min())
    d_c = float(df["close"].iloc[-1])
    d_p = (d_h + d_l + d_c)/3
    d_s1 = 2*d_p - d_h; d_r1 = 2*d_p - d_l
    d_s2 = d_p - (d_h - d_l); d_r2 = d_p + (d_h - d_l)

    w_h = float(df["high"].tail(168).max())
    w_l = float(df["low"].tail(168).min())
    w_c = float(df["close"].iloc[-1])
    w_p = (w_h + w_l + w_c)/3
    w_s1 = 2*w_p - w_h; w_r1 = 2*w_p - w_l

    return {"d_s2":d_s2, "d_s1":d_s1, "d_p":d_p, "d_r1":d_r1, "d_r2":d_r2, "w_s1":w_s1, "w_r1":w_r1}

def CANDLES(df):
    res = {"buy":[], "sell":[]}
    if len(df) < 5: return res
    O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values

    def body(i): return abs(C[i]-O[i]) + 1e-9
    def bull(i): return C[i] > O[i]
    def bear(i): return C[i] < O[i]
    def upper_wick(i): return H[i] - max(C[i], O[i])
    def lower_wick(i): return min(C[i], O[i]) - L[i]

    i = -1
    if bull(i) and lower_wick(i) >= 2*body(i) and upper_wick(i) < 0.2*body(i):
        res["buy"].append("Hammer ↑")
    if bear(i) and upper_wick(i) >= 2*body(i) and lower_wick(i) < 0.2*body(i):
        res["sell"].append("Shooting Star ↓")
    if bear(-2) and bull(i) and C[i] > O[-2] and O[i] < L[-2] and body(i) > body(-2)*1.2:
        res["buy"].append("Engulfing ↑")
    if bull(-2) and bear(i) and C[i] < O[-2] and O[i] > H[-2] and body(i) > body(-2)*1.2:
        res["sell"].append("Engulfing ↓")
    if abs(C[i]-O[i]) < (H[i]-L[i])*0.1 and abs(lower_wick(i) - upper_wick(i)) > 1.5*body(i):
        if lower_wick(i) > upper_wick(i):
            res["buy"].append("Doji Hammer ↑")
        else:
            res["sell"].append("Doji Shooting ↓")
    return res

# ==============================================
# 🛡️ فلاتر الحماية والوقت
# ==============================================
def is_good_time():
    now = datetime.utcnow()
    if now.weekday() in AVOID_DAYS or now.hour in AVOID_HOURS:
        return False
    return True

def check_risk_limits():
    global TRADING_ENABLED, daily_pnl, consecutive_losses
    if not TRADING_ENABLED: return False
    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
        TRADING_ENABLED = False
        logging.warning("توقف: وصل لحد الخسائر المتتالية")
        return False
    if daily_pnl <= -BALANCE * (MAX_DAILY_LOSS / 100):
        TRADING_ENABLED = False
        logging.warning("توقف: وصل للحد الأقصى للخسارة اليومية")
        return False
    return True

# ==============================================
# 🧮 منطق التحليل المحسّن والاحترافي
# ==============================================
def analyze(df):
    if df is None or df.empty or len(df) < 60: return None, 0, {}
    if not is_good_time() or not check_risk_limits(): return None, 0, {}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    price = float(close.iloc[-1])

    # المؤشرات الأساسية
    e20 = EMA(close,20).iloc[-1]
    e50 = EMA(close,50).iloc[-1]
    e100 = EMA(close,100).iloc[-1]
    adx, pdi, ndi = ADX_Wilder(high, low, close)
    adx_val = float(adx.iloc[-1])
    st_val, st_trend = SuperTrend(high, low, close)
    tenkan, kijun, senkou_a, senkou_b, chikou = Ichimoku(high, low, close)
    psar, psar_trend = Parabolic_SAR(high, low)
    bb_u, bb_m, bb_l, bb_width = BB(close)
    atr_val = max(float(ATR_Wilder(high, low, close).iloc[-1]), 1e-5)
    ha = Heikin_Ashi(df)

    trend_buy = 0
    trend_sell = 0
    reasons = []

    # فلتر اتجاه عام
    if adx_val < ADX_THRESHOLD:
        return None,0,{}
    if bb_width < BB_WIDTH_THRESHOLD:
        return None,0,{}

    # 1. قوة الاتجاه
    if pdi.iloc[-1] > ndi.iloc[-1]:
        trend_buy += WEIGHTS["adx"]
        reasons.append(f"ADX قوي ↑ ({adx_val:.1f})")
    else:
        trend_sell += WEIGHTS["adx"]
        reasons.append(f"ADX قوي ↓ ({adx_val:.1f})")

    if e20 > e50 > e100 and price > e20:
        trend_buy += WEIGHTS["ema"]
        reasons.append("تسلسل إيجابي EMA")
    elif e20 < e50 < e100 and price < e20:
        trend_sell += WEIGHTS["ema"]
        reasons.append("تسلسل سلبي EMA")

    if st_trend.iloc[-1] == 1 and price > st_val.iloc[-1]:
        trend_buy += WEIGHTS["supertrend"]
        reasons.append("SuperTrend صاعد")
    elif st_trend.iloc[-1] == -1 and price < st_val.iloc[-1]:
        trend_sell += WEIGHTS["supertrend"]
        reasons.append("SuperTrend هابط")

    if price > senkou_a.iloc[-1] and price > senkou_b.iloc[-1] and tenkan.iloc[-1] > kijun.iloc[-1] and chikou.iloc[-1] > close.iloc[-26]:
        trend_buy += WEIGHTS["ichimoku"]
        reasons.append("إيشيموكو إيجابي")
    elif price < senkou_a.iloc[-1] and price < senkou_b.iloc[-1] and tenkan.iloc[-1] < kijun.iloc[-1] and chikou.iloc[-1] < close.iloc[-26]:
        trend_sell += WEIGHTS["ichimoku"]
        reasons.append("إيشيموكو سلبي")

    # 2. الزخم والقوة
    rsi = float(RSI(close,14).iloc[-1])
    macd, macd_sig = MACD(close)
    stoch_k = float(STOCH(high, low, close).iloc[-1])
    cci_val = float(CCI(high, low, close).iloc[-1])
    mom = float(MOMENTUM(close,10).iloc[-1])

    if rsi < 30:
        trend_buy += WEIGHTS["rsi"]
        reasons.append(f"RSI {rsi:.0f} منطقة شراء")
    elif rsi > 70:
        trend_sell += WEIGHTS["rsi"]
        reasons.append(f"RSI {rsi:.0f} منطقة بيع")

    if macd.iloc[-2] < macd_sig.iloc[-2] and macd.iloc[-1] > macd_sig.iloc[-1]:
        trend_buy += WEIGHTS["macd"]
        reasons.append("تقاطع MACD صاعد")
    elif macd.iloc[-2] > macd_sig.iloc[-2] and macd.iloc[-1] < macd_sig.iloc[-1]:
        trend_sell += WEIGHTS["macd"]
        reasons.append("تقاطع MACD هابط")

    if cci_val < -CCI_THRESHOLD:
        trend_buy += WEIGHTS["cci"]
        reasons.append(f"CCI {cci_val:.0f} تشبع بيع")
    elif cci_val > CCI_THRESHOLD:
        trend_sell += WEIGHTS["cci"]
        reasons.append(f"CCI {cci_val:.0f} تشبع شراء")

    if mom > 0:
        trend_buy += WEIGHTS["momentum"]
    else:
        trend_sell += WEIGHTS["momentum"]

    # 3. مناطق الدعم والمقاومة
    _, _, k_l = Keltner(high, low, close)
    _, _, d_l = Donchian(high, low)
    _, _, k_u = Keltner(high, low, close)
    _, _, d_u = Donchian(high, low)
    pivots = Daily_Weekly_Pivot(df)
    vol = float(VOLATILITY(close,20).iloc[-1])

    if price <= min(bb_l.iloc[-1], k_l.iloc[-1], d_l.iloc[-1]) * 1.01:
        trend_buy += WEIGHTS["bollinger"] + WEIGHTS["keltner"] + WEIGHTS["donchian"]
        reasons.append("منطقة دعم متعددة")
    if price >= max(bb_u.iloc[-1], k_u.iloc[-1], d_u.iloc[-1]) * 0.99:
        trend_sell += WEIGHTS["bollinger"] + WEIGHTS["keltner"] + WEIGHTS["donchian"]
        reasons.append("منطقة مقاومة متعددة")

    if abs(price - pivots["d_s1"]) < atr_val * 1.0 or abs(price - pivots["w_s1"]) < atr_val * 1.2:
        trend_buy += WEIGHTS["pivot"]
        reasons.append("قريب من دعم محوري")
    if abs(price - pivots["d_r1"]) < atr_val * 1.0 or abs(price - pivots["w_r1"]) < atr_val * 1.2:
        trend_sell += WEIGHTS["pivot"]
        reasons.append("قريب من مقاومة محورية")

    # 4. أنماط الشموع
    patterns = CANDLES(df)
    for pat in patterns["buy"]:
        trend_buy += 3
        reasons.append(pat)
    for pat in patterns["sell"]:
        trend_sell += 3
        reasons.append(pat)

    # فلتر نهائي
    score = max(trend_buy, trend_sell)
    if score < MIN_SCORE:
        return None, 0, {}
    if abs(trend_buy - trend_sell) < 4:
        return None,0,{}

    conf = min(98, int((score / (trend_buy + trend_sell)) * 100))
    power = min(100, int(score * 2.2))
    stake = round(BALANCE * (RISK_PCT / 100), 2)
    profit = round(stake * (PAYOUT / 100), 2)

    det = {
        "price": round(price,5), "rsi": round(rsi,1), "adx": round(adx_val,1),
        "atr": round(atr_val,5), "conf": conf, "power": power,
        "stake": stake, "profit": profit,
        "sl_b": round(price - 2.0*atr_val,5), "tp_b": round(price + 3.0*atr_val,5),
        "sl_s": round(price + 2.0*atr_val,5), "tp_s": round(price - 3.0*atr_val,5),
        "why": " | ".join(reasons)
    }

    return ("BUY", score, det) if trend_buy > trend_sell else ("SELL", score, det)

# ==============================================
# 📊 تحليل متعدد الأطر الزمنية
# ==============================================
def get_multi_timeframe_signal(pair):
    ticker = SYMBOLS[pair]
    main_cfg = TIMEFRAMES[MAIN_TF]
    df_main = fetch(ticker, main_cfg["interval"], main_cfg["period"], main_cfg["bars"])
    sig_main, score_main, det_main = analyze(df_main)
    if not sig_main: return None,0,{},0

    confirm_count = 0
    confirm_list = []
    for tf in CONFIRM_TFS:
        cfg = TIMEFRAMES[tf]
        df_tf = fetch(ticker, cfg["interval"], cfg["period"], cfg["bars"])
        sig_tf, score_tf, _ = analyze(df_tf)
        if sig_tf == sig_main:
            confirm_count += 1
            confirm_list.append(cfg["label"])

    if confirm_count >= MIN_CONFIRM:
        det_main["confirm"] = f"✅ مؤكد من: {', '.join(confirm_list)}"
        return sig_main, score_main + 3, det_main, confirm_count
    return None,0,{},0

# ==============================================
# 📝 السجل والتقارير
# ==============================================
def log_signal(pair, sig, score, power, conf, tf, exp, price):
    with lock:
        exists = os.path.isfile(LOG_FILE)
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["التاريخ","الزوج","الإشارة","النقاط","القوة","الثقة","الإطار","المدة","السعر_الدخول"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                pair, sig, score, power, conf, tf, exp, round(price,5)
            ])

def get_report():
    if not os.path.isfile(LOG_FILE):
        return "📈 لا يوجد سجل إشارات حتى الآن"
    try:
        df = pd.read_csv(LOG_FILE)
        total = len(df)
        buy = len(df[df["الإشارة"]=="BUY"])
        sell = len(df[df["الإشارة"]=="SELL"])
        avg_p = round(df["القوة"].mean(),1)
        avg_c = round(df["الثقة"].mean(),1)
        win_rate = "غير محسوب"
        return (
            f"📊 <b>تقرير الأداء</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"إجمالي الإشارات: {total}\n"
            f"🟢 شراء: {buy} | 🔴 بيع: {sell}\n"
            f"متوسط القوة: {avg_p}/100 | الثقة: {avg_c}%\n"
            f"حالة التداول: {'مفعل ✅' if TRADING_ENABLED else 'متوقف ⛔'}"
        )
    except:
        return "📈 سجل فارغ"

# ==============================================
# 📄 تنسيق الرسائل
# ==============================================
def format_signal(pair, tf, sig, det, exp):
    arrow = "▲" if sig=="BUY" else "▼"
    color = "🟢" if sig=="BUY" else "🔴"
    sl = det["sl_b"] if sig=="BUY" else det["sl_s"]
    tp = det["tp_b"] if sig=="BUY" else det["tp_s"]

    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK PRO v3.0</b> • {otc(pair)}\n"
        f"⏱ {tf} | {exp}\n{det.get('confirm','')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow} {color} <b>{sig}</b> {arrow}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💪 القوة: <code>{det['power']}/100</code> | الثقة: <code>{det['conf']}%</code>\n"
        f"💰 السعر: <code>{det['price']}</code>\n"
        f"📊 ADX: <code>{det['adx']}</code> | RSI: <code>{det['rsi']}</code>\n"
        f"🛑 إيقاف الخسارة: <code>{sl}</code> | الهدف: <code>{tp}</code>\n"
        f"💵 المخاطرة: <code>${det['stake']}</code> | الربح: <code>+${det['profit']}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 السبب: <i>{det['why']}</i>\n"
        f"🕒 {datetime.now().strftime('%H:%M:%S')}\n"
        "⚠️ لأغراض تحليلية فقط — ليس توصية تداول\n"
    )

def format_no_signal(pair, tf):
    return f"━━━━━━━━━━━━━━━━━━━━\n⚡ {otc(pair)} | {tf}\n⚠️ لا توجد إشارة واضحة حالياً\n━━━━━━━━━━━━━━━━━━━━"

# ==============================================
# 🔍 عملية الفحص
# ==============================================
def do_scan(chat_id=None, pair=None, tfk=None):
    tgt = chat_id or CHAT_ID
    if pair and tfk:
        cfg = TIMEFRAMES[tfk]
        df = fetch(SYMBOLS[pair], cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)
        if sig:
            send(tgt, format_signal(pair, cfg["label"], sig, det, cfg["exp"]), kb_result())
            log_signal(pair, sig, score, det["power"], det["conf"], cfg["label"], cfg["exp"], det["price"])
        else:
            send(tgt, format_no_signal(pair, cfg["label"]), kb_result())
        return

    found = []
    for name in SYMBOLS:
        time.sleep(FETCH_DELAY)
        sig, score, det, confirm = get_multi_timeframe_signal(name)
        if not sig: continue
        key = f"{name}|{MAIN_TF}|{sig}"
        if time.time() - last_sig.get(key,0) < COOLDOWN: continue
        last_sig[key] = time.time()
        found.append((name, TIMEFRAMES[MAIN_TF]["label"], sig, score, det, TIMEFRAMES[MAIN_TF]["exp"]))

    if not found:
        send(tgt, "⚠️ لا توجد إشارات مؤكدة حالياً", kb_main())
        return

    for name, tf, sig, score, det, exp in found:
        send(tgt, format_signal(name, tf, sig, det, exp), kb_result())
        log_signal(name, sig, score, det["power"], det["conf"], tf, exp, det["price"])
        time.sleep(1)

# ==============================================
# 🤖 معالجة الأوامر
# ==============================================
def on_command(cid, txt):
    if txt.strip() in ["/start", "/menu"]:
        send(cid,
            "⚡ <b>BASILISK PRO v3.0</b>\n\n"
            "✅ مؤشرات احترافية متقدمة\n"
            "✅ تحليل متعدد الأطر الزمنية\n"
            "✅ إدارة مخاطر ذكية\n"
            "✅ أنماط شموع معترف بها\n"
            "✅ فلتر اتجاه وزخم قوي\n\n"
            "اختر من القائمة:", kb_main())

def on_callback(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "القائمة الرئيسية", kb_main())
    elif data == "SCANALL":
        edit(cid, mid, "🔍 جاري الفحص... انتظر قليلاً")
        threading.Thread(target=do_scan, args=(cid,), daemon=True).start()
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر الزوج:", kb_pairs())
    elif data == "REPORT":
        edit(cid, mid, get_report(), kb_main())
    elif data == "RISK":
        s = round(BALANCE * (RISK_PCT/100),2)
        p = round(s * (PAYOUT/100),2)
        edit(cid, mid,
            f"💰 <b>إدارة المخاطر</b>\n"
            f"الرصيد: ${BALANCE}\n"
            f"المخاطرة لكل صفقة: {RISK_PCT}% = ${s}\n"
            f"الربح المتوقع: +${p}\n"
            f"الحد الأقصى للخسارة اليومية: {MAX_DAILY_LOSS}%\n"
            f"توقف تلقائي بعد {MAX_CONSECUTIVE_LOSSES} خسائر متتالية", kb_main())
    elif data.startswith("P:"):
        edit(cid, mid, f"⏱ اختر الإطار لـ {otc(data[2:])}", kb_tf(data[2:]))
    elif data.startswith("T:"):
        _, pr, tf = data.split(":")
        edit(cid, mid, f"🔍 جاري تحليل {otc(pr)}...")
        threading.Thread(target=do_scan, args=(cid, pr, tf), daemon=True).start()

# ==============================================
# 🚀 التشغيل
# ==============================================
def polling():
    last = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset":last+1, "timeout":30, "allowed_updates":json.dumps(["message","callback_query"])},
                timeout=35
            ).json()
            for upd in res.get("result", []):
                last = upd["update_id"]
                if "message" in upd:
                    on_command(upd["message"]["chat"]["id"], upd["message"].get("text",""))
                if "callback_query" in upd:
                    cb = upd["callback_query"]
                    on_callback(cb["message"]["chat"]["id"], cb["message"]["message_id"], cb["id"], cb["data"])
        except Exception as e:
            logging.error(f"Polling: {e}")
            time.sleep(3)

@app.route("/")
def home():
    return "✅ BASILISK PRO v3.0 • يعمل بنجاح"

if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "ضع_رمز_البوت_هنا":
        print("❌ أدخل BOT_TOKEN أولاً")
    else:
        threading.Thread(target=polling, daemon=True).start()
        port = int(os.environ.get("PORT", 8080))
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port)
        except:
            app.run(host="0.0.0.0", port=port)
