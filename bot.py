import os
import time
import json
import threading
import logging
import csv
from datetime import datetime, timedelta
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ==============================================
# ⚙️ الإعدادات العامة - معدلة لزيادة دقة الإشارات
# ==============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 85

# إدارة مخاطر
MAX_DAILY_LOSS = 8.0
MAX_CONSECUTIVE_LOSSES = 4
TRADING_ENABLED = True
SPREAD = 0.0002

# ✅ تحسين مصادر البيانات لدعم أسواق OTC بشكل أفضل
SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "CAD/CHF": "CADCHF=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "GBP/AUD": "GBPAUD=X", "XAU/USD": "GC=F", "XAG/USD": "SI=F",
}

# 🕒 الأطر الزمنية
TIMEFRAMES = {
    "2د":   {"interval": "2m",  "period": "1d",  "label": "2 دقائق",  "bars": 50, "exp": "02:00"},
    "3د":   {"interval": "3m",  "period": "2d",  "label": "3 دقائق",  "bars": 50, "exp": "03:00"},
    "5د":   {"interval": "5m",  "period": "3d",  "label": "5 دقائق",  "bars": 50, "exp": "05:00"},
    "10د":  {"interval": "10m", "period": "5d",  "label": "10 دقيقة", "bars": 60, "exp": "10:00"},
    "15د":  {"interval": "15m", "period": "7d",  "label": "15 دقيقة", "bars": 60, "exp": "15:00"},
    "30د":  {"interval": "30m", "period": "10d", "label": "30 دقيقة", "bars": 70, "exp": "30:00"},
    "1س":   {"interval": "60m", "period": "20d", "label": "ساعة",     "bars": 80, "exp": "01:00:00"},
}

# ⚙️ إعدادات التحليل - **خفضت الحدود الصارمة جداً**
MAIN_TF        = "5د"
CONFIRM_TFS    = ["10د"]          # تقليل عدد التأكيد لزيادة الإشارات
MIN_CONFIRM    = 0                # إيقاف شرط التأكيد الإجباري
MIN_SCORE      = 4                # خفض الحد الأدنى للنقاط
ADX_THRESHOLD  = 18               # خفض حد ADX لالتقاط اتجاهات أضعف
BB_WIDTH_THRESHOLD = 0.0005       # خفض حد عرض البولينجر
CCI_THRESHOLD = 70                # خفض حد CCI
COOLDOWN       = 180              # تقليل فترة الانتظار بين الإشارات
FETCH_DELAY    = 1.2
MAX_FETCH_RETRIES = 4

# ⚖️ أوزان المؤشرات الجديدة - أكثر توازناً وملاءمة
WEIGHTS = {
    "adx": 2, "ema": 3, "supertrend": 3, "ichimoku": 2, "parabolic": 2,
    "rsi": 3, "macd": 4, "stoch": 3, "cci": 2, "momentum": 2,
    "bollinger": 2, "keltner": 2, "donchian": 2,
    "pivot": 2, "candles": 4       # زيادة وزن أنماط الشموع
}

LOG_FILE = "signals_log.csv"
last_sig = {}
consecutive_losses = 0
daily_pnl = 0.0
lock = threading.Lock()

# 📅 فلاتر الوقت - **خففتها**
AVOID_HOURS = []       # ألغيت الحظر الكلي
AVOID_DAYS = []        # ألغيت حظر الجمعة

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
        [{"text": "❓ مساعدة", "callback_data": "HELP"}]
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
# 📊 جلب البيانات المحسّنة - معالجة أخطاء أفضل
# ==============================================
def fetch(ticker, interval, period, min_bars=40):
    retries = 0
    while retries < MAX_FETCH_RETRIES:
        try:
            # ✅ تحسين جلب البيانات لضمان الحصول على كافة الشموع
            df = yf.download(
                ticker,
                interval=interval,
                period=period,
                progress=False,
                auto_adjust=False,
                timeout=20,
                prepost=True
            )
            if df.empty:
                retries += 1
                time.sleep(1.5)
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower().strip() for c in df.columns]

            req_cols = ["open", "high", "low", "close"]
            if not set(req_cols).issubset(df.columns):
                retries += 1
                time.sleep(1.5)
                continue

            df = df[req_cols].dropna()
            df = df[df["high"] != df["low"]]

            if len(df) < min_bars:
                retries += 1
                time.sleep(1.5)
                continue

            return df.copy()

        except Exception as e:
            logging.warning(f"محاولة جلب {ticker} رقم {retries+1} فشلت: {str(e)[:50]}")
            retries += 1
            time.sleep(2)

    logging.error(f"❌ فشل جلب بيانات {ticker} بعد {MAX_FETCH_RETRIES} محاولات")
    return pd.DataFrame()

# ==============================================
# 📊 المؤشرات الفنية - محسنة ومبسطة
# ==============================================
def EMA(s, p): return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    gain = d.where(d > 0, 0).ewm(alpha=1/p, adjust=False).mean()
    loss = (-d.where(d < 0, 0)).ewm(alpha=1/p, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def MACD(s, fast=12, slow=26, signal=9):
    ema_fast = EMA(s, fast)
    ema_slow = EMA(s, slow)
    macd_line = ema_fast - ema_slow
    signal_line = EMA(macd_line, signal)
    return macd_line, signal_line

def BB(s, p=20):
    mid = s.rolling(p).mean()
    std = s.rolling(p).std(ddof=0)
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = (upper - lower) / (mid + 1e-9)
    return upper, mid, lower, width

def ATR(h, l, c, p=14):
    tr = pd.concat([h - l, abs(h - c.shift()), abs(l - c.shift())], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()

def ADX(h, l, c, p=14):
    up = h.diff()
    down = -l.diff()
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr = ATR(h, l, c, 1)

    pdm_roll = pd.Series(pdm, index=h.index).rolling(p).mean()
    ndm_roll = pd.Series(ndm, index=h.index).rolling(p).mean()
    atr_roll = tr.rolling(p).mean().replace(0, 1e-9)

    pdi = 100 * pdm_roll / atr_roll
    ndi = 100 * ndm_roll / atr_roll
    dx = 100 * abs(pdi - ndi) / (pdi + ndi + 1e-9)
    adx = dx.rolling(p).mean()
    return adx, pdi, ndi

def STOCH(h, l, c, k=14, d=3):
    ll = l.rolling(k).min()
    hh = h.rolling(k).max()
    k_line = 100 * (c - ll) / (hh - ll + 1e-9)
    d_line = k_line.rolling(d).mean()
    return k_line, d_line

def SuperTrend(h, l, c, p=10, m=1.5):
    atr = ATR(h, l, c, p)
    hl2 = (h + l) / 2
    upper = hl2 + m * atr
    lower = hl2 - m * atr
    trend = pd.Series(1, index=c.index)

    for i in range(1, len(c)):
        if c.iloc[i-1] <= upper.iloc[i-1]:
            upper.iloc[i] = min(upper.iloc[i], upper.iloc[i-1])
        if c.iloc[i-1] >= lower.iloc[i-1]:
            lower.iloc[i] = max(lower.iloc[i], lower.iloc[i-1])

        if c.iloc[i] > upper.iloc[i]:
            trend.iloc[i] = 1
        elif c.iloc[i] < lower.iloc[i]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]

    return lower.where(trend==1, upper), trend

def Ichimoku(h, l, c):
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun)/2).shift(26)
    senkou_b = ((h.rolling(52).max() + l.rolling(52).min())/2).shift(26)
    chikou = c.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou

def CCI(h, l, c, p=20):
    tp = (h + l + c) / 3
    ma = tp.rolling(p).mean()
    md = tp.rolling(p).apply(lambda x: np.mean(np.abs(x - np.mean(x))))
    return (tp - ma) / (0.015 * (md + 1e-9))

def Parabolic_SAR(h, l, step=0.02, max_step=0.2):
    sar = pd.Series(index=h.index, dtype=float)
    trend = pd.Series(1, index=h.index)
    ep = l.iloc[0]
    af = step
    sar.iloc[0] = ep

    for i in range(1, len(h)):
        sar.iloc[i] = sar.iloc[i-1] + af * (ep - sar.iloc[i-1])
        if trend.iloc[i-1] == 1:
            if l.iloc[i] < sar.iloc[i]:
                trend.iloc[i] = -1
                sar.iloc[i] = ep
                ep = h.iloc[i]
                af = step
            else:
                trend.iloc[i] = 1
                if h.iloc[i] > ep:
                    ep = h.iloc[i]
                    af = min(af + step, max_step)
        else:
            if h.iloc[i] > sar.iloc[i]:
                trend.iloc[i] = 1
                sar.iloc[i] = ep
                ep = l.iloc[i]
                af = step
            else:
                trend.iloc[i] = -1
                if l.iloc[i] < ep:
                    ep = l.iloc[i]
                    af = min(af + step, max_step)
    return sar, trend

def PIVOT(df):
    h = float(df["high"].tail(20).max())
    l = float(df["low"].tail(20).min())
    c = float(df["close"].iloc[-1])
    p = (h + l + c) / 3
    return {"s2": p - (h - l)*1.1, "s1": p - (h - l)*0.5, "p": p, "r1": p + (h - l)*0.5, "r2": p + (h - l)*1.1}

def CANDLES(df):
    res = {"buy": [], "sell": []}
    if len(df) < 3: return res
    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values

    def body(i): return abs(C[i]-O[i]) + 1e-9
    def wick_up(i): return H[i] - max(C[i], O[i])
    def wick_dn(i): return min(C[i], O[i]) - L[i]

    i = -1
    # مطرقة
    if C[i] > O[i] and wick_dn(i) > 2 * body(i) and wick_up(i) < 0.5 * body(i):
        res["buy"].append("مطرقة ↑")
    # نجم الرماية
    if C[i] < O[i] and wick_up(i) > 2 * body(i) and wick_dn(i) < 0.5 * body(i):
        res["sell"].append("نجم رماية ↓")
    # ابتلاع
    if C[i] > O[i] and O[i] < L[-2] and C[i] > H[-2] and body(i) > body(-2):
        res["buy"].append("ابتلاع إيجابي ↑")
    if C[i] < O[i] and O[i] > H[-2] and C[i] < L[-2] and body(i) > body(-2):
        res["sell"].append("ابتلاع سلبي ↓")

    return res

# ==============================================
# 🧮 منطق التحليل الجديد - **أقوى وأكثر إعطاءً للإشارات**
# ==============================================
def analyze(df):
    if df is None or df.empty or len(df) < 40:
        return None, 0, {}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    price = float(close.iloc[-1])

    # حساب المؤشرات
    adx_val, pdi, ndi = ADX(high, low, close)
    adx_val = float(adx_val.iloc[-1])

    e20 = float(EMA(close, 20).iloc[-1])
    e50 = float(EMA(close, 50).iloc[-1])

    rsi_val = float(RSI(close, 14).iloc[-1])
    macd_line, sig_line = MACD(close)
    macd_cross = (macd_line.iloc[-1] - sig_line.iloc[-1])

    bb_u, bb_m, bb_l, bb_w = BB(close)
    st_val, st_trend = SuperTrend(high, low, close)
    tenkan, kijun, _, _, _ = Ichimoku(high, low, close)
    psar_val, psar_trend = Parabolic_SAR(high, low)
    k_line, _ = STOCH(high, low, close)
    cci_val = float(CCI(high, low, close).iloc[-1])
    pivots = PIVOT(df)
    atr_val = max(float(ATR(high, low, close).iloc[-1]), 1e-9)
    candles = CANDLES(df)

    score_buy = 0
    score_sell = 0
    reasons = []

    # ✅ فلتر أخف بكثير
    if adx_val < ADX_THRESHOLD and abs(pdi.iloc[-1] - ndi.iloc[-1]) < 8:
        return None, 0, {}

    # 1. الاتجاه
    if e20 > e50 and price > e20:
        score_buy += WEIGHTS["ema"]
        reasons.append("متوسطات صاعدة")
    elif e20 < e50 and price < e20:
        score_sell += WEIGHTS["ema"]
        reasons.append("متوسطات هابطة")

    if st_trend.iloc[-1] == 1 and price > st_val.iloc[-1]:
        score_buy += WEIGHTS["supertrend"]
        reasons.append("سوبرترند صاعد")
    elif st_trend.iloc[-1] == -1 and price < st_val.iloc[-1]:
        score_sell += WEIGHTS["supertrend"]
        reasons.append("سوبرترند هابط")

    # 2. الزخم
    if rsi_val < 40:
        score_buy += WEIGHTS["rsi"]
        reasons.append(f"RSI {rsi_val:.0f}")
    elif rsi_val > 60:
        score_sell += WEIGHTS["rsi"]
        reasons.append(f"RSI {rsi_val:.0f}")

    if macd_cross > 0 and macd_line.iloc[-2] < sig_line.iloc[-2]:
        score_buy += WEIGHTS["macd"]
        reasons.append("تقاطع MACD صاعد")
    elif macd_cross < 0 and macd_line.iloc[-2] > sig_line.iloc[-2]:
        score_sell += WEIGHTS["macd"]
        reasons.append("تقاطع MACD هابط")

    if k_line < 30:
        score_buy += WEIGHTS["stoch"]
        reasons.append("ستوكاستيك منخفض")
    elif k_line > 70:
        score_sell += WEIGHTS["stoch"]
        reasons.append("ستوكاستيك مرتفع")

    # 3. مناطق الدعم والمقاومة
    if price <= bb_l.iloc[-1] * 1.02 or price <= pivots["s1"]:
        score_buy += WEIGHTS["bollinger"] + WEIGHTS["pivot"]
        reasons.append("منطقة دعم")
    if price >= bb_u.iloc[-1] * 0.98 or price >= pivots["r1"]:
        score_sell += WEIGHTS["bollinger"] + WEIGHTS["pivot"]
        reasons.append("منطقة مقاومة")

    # 4. أنماط الشموع
    for pat in candles["buy"]:
        score_buy += WEIGHTS["candles"]
        reasons.append(pat)
    for pat in candles["sell"]:
        score_sell += WEIGHTS["candles"]
        reasons.append(pat)

    # ✅ شرط الفصل أخف
    if max(score_buy, score_sell) < MIN_SCORE or abs(score_buy - score_sell) < 2:
        return None, 0, {}

    final_sig = "BUY" if score_buy > score_sell else "SELL"
    final_score = max(score_buy, score_sell)
    conf = min(95, int((final_score / (score_buy + score_sell + 1e-9)) * 100))

    stake = round(BALANCE * RISK_PCT / 100, 2)
    profit = round(stake * PAYOUT / 100, 2)

    det = {
        "price": round(price, 5),
        "rsi": round(rsi_val, 1),
        "adx": round(adx_val, 1),
        "atr": round(atr_val, 5),
        "conf": conf,
        "power": min(100, int(final_score * 3)),
        "stake": stake,
        "profit": profit,
        "sl_b": round(price - 1.8 * atr_val, 5),
        "tp_b": round(price + 2.5 * atr_val, 5),
        "sl_s": round(price + 1.8 * atr_val, 5),
        "tp_s": round(price - 2.5 * atr_val, 5),
        "why": " | ".join(reasons)
    }

    return final_sig, final_score, det

# ==============================================
# 📊 تحليل متعدد الأطر الزمنية
# ==============================================
def get_multi_timeframe_signal(pair):
    ticker = SYMBOLS[pair]
    main_cfg = TIMEFRAMES[MAIN_TF]
    df_main = fetch(ticker, main_cfg["interval"], main_cfg["period"], main_cfg["bars"])
    sig_main, score_main, det_main = analyze(df_main)

    if not sig_main:
        return None, 0, {}, 0

    confirm = 0
    for tf in CONFIRM_TFS:
        cfg = TIMEFRAMES[tf]
        df = fetch(ticker, cfg["interval"], cfg["period"], cfg["bars"])
        sig, _, _ = analyze(df)
        if sig == sig_main:
            confirm += 1

    if confirm >= MIN_CONFIRM:
        det_main["confirm"] = f"✅ مؤكد"
        return sig_main, score_main + 1, det_main, confirm

    return sig_main, score_main, det_main, confirm

# ==============================================
# 📝 السجل والتقارير
# ==============================================
def log_signal(pair, sig, score, power, conf, tf, exp, price):
    with lock:
        exists = os.path.isfile(LOG_FILE)
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["التاريخ","الزوج","الإشارة","النقاط","القوة","الثقة","الإطار","المدة","السعر"])
            w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), pair, sig, score, power, conf, tf, exp, price])

def get_report():
    if not os.path.isfile(LOG_FILE):
        return "📈 لا يوجد إشارات حتى الآن"
    try:
        df = pd.read_csv(LOG_FILE)
        return (
            f"📊 <b>التقرير</b>\n"
            f"إجمالي الإشارات: {len(df)}\n"
            f"🟢 شراء: {len(df[df['الإشارة']=='BUY'])}\n"
            f"🔴 بيع: {len(df[df['الإشارة']=='SELL'])}\n"
            f"متوسط الثقة: {round(df['الثقة'].mean(),1)}%"
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
        f"⚡ <b>BASILISK PRO v3.1</b> • {otc(pair)}\n"
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
        "⚠️ لأغراض تحليلية فقط\n"
    )

def format_no_signal(pair, tf):
    return f"━━━━━━━━━━━━━━━━━━━━\n⚡ {otc(pair)} | {tf}\nℹ️ لا إشارة قوية حالياً\n━━━━━━━━━━━━━━━━━━━━"

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
        if not sig:
            continue
        key = f"{name}|{MAIN_TF}|{sig}"
        if time.time() - last_sig.get(key, 0) < COOLDOWN:
            continue
        last_sig[key] = time.time()
        found.append((name, TIMEFRAMES[MAIN_TF]["label"], sig, score, det, TIMEFRAMES[MAIN_TF]["exp"]))

    if not found:
        send(tgt, "ℹ️ لا توجد إشارات قوية حالياً", kb_main())
        return

    for name, tf, sig, score, det, exp in found:
        send(tgt, format_signal(name, tf, sig, det, exp), kb_result())
        log_signal(name, sig, score, det["power"], det["conf"], tf, exp, det["price"])
        time.sleep(0.8)

# ==============================================
# 🤖 معالجة الأوامر
# ==============================================
def on_command(cid, txt):
    if txt.strip() in ["/start", "/menu"]:
        send(cid,
            "⚡ <b>BASILISK PRO v3.1</b>\n\n"
            "✅ إشارات أوضح وأكثر تواتراً\n"
            "✅ فلاتر ذكية ومتوازنة\n"
            "✅ دعم أفضل لأسواق OTC\n"
            "✅ مؤشرات مُحسنة للتداول السريع\n\n"
            "اختر من القائمة:", kb_main())

def on_callback(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "القائمة الرئيسية", kb_main())
    elif data == "SCANALL":
        edit(cid, mid, "🔍 جاري الفحص...")
        threading.Thread(target=do_scan, args=(cid,), daemon=True).start()
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر الزوج:", kb_pairs())
    elif data == "REPORT":
        edit(cid, mid, get_report(), kb_main())
    elif data == "RISK":
        s = round(BALANCE * RISK_PCT / 100, 2)
        p = round(s * PAYOUT / 100, 2)
        edit(cid, mid,
            f"💰 <b>إدارة المخاطر</b>\n"
            f"الرصيد: ${BALANCE}\nالمخاطرة: {RISK_PCT}% = ${s}\nالربح: +${p}", kb_main())
    elif data.startswith("P:"):
        edit(cid, mid, f"⏱ اختر الإطار لـ {otc(data[2:])}", kb_tf(data[2:]))
    elif data.startswith("T:"):
        _, pr, tf = data.split(":")
        edit(cid, mid, f"🔍 تحليل {otc(pr)}...")
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
                params={"offset": last + 1, "timeout": 30, "allowed_updates": json.dumps(["message","callback_query"])},
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
            logging.error(f"Polling Error: {e}")
            time.sleep(3)

@app.route("/")
def home():
    return "✅ BASILISK PRO v3.1 يعمل بنجاح"

if __name__ == "__main__":
    if not BOT_TOKEN or "..." in BOT_TOKEN:
        print("❌ أدخل رمز البوت أولاً")
    else:
        threading.Thread(target=polling, daemon=True).start()
        port = int(os.environ.get("PORT", 8080))
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port)
        except:
            app.run(host="0.0.0.0", port=port)
