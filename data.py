import os
import time
import requests
import threading
import logging
from datetime import datetime

import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ══════════════════════════════════════════════════════════════
#  إعدادات
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8720697433:AAGTkLVCKb6lzMdVM2NosXNMWn4eRaqg0FQ")
CHAT_ID   = int(os.environ.get("CHAT_ID", "8674500253"))

SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X", "AUD/USD": "AUDUSD=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X", "CAD/CHF": "CADCHF=X",
    "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X", "GBP/AUD": "GBPAUD=X",
    "XAU/USD": "GC=F",
}

# ✅ الأطر الزمنية المطلوبة
TIMEFRAMES = {
    "1د":  {"interval": "1m",  "period": "1d",  "label": "1 دقيقة",  "min_bars": 60},
    "2د":  {"interval": "2m",  "period": "1d",  "label": "2 دقيقة",  "min_bars": 60},
    "3د":  {"interval": "5m",  "period": "2d",  "label": "3 دقيقة",  "min_bars": 60},
    "5د":  {"interval": "5m",  "period": "5d",  "label": "5 دقائق",  "min_bars": 60},
    "15د": {"interval": "15m", "period": "10d", "label": "15 دقيقة", "min_bars": 60},
    "30د": {"interval": "30m", "period": "15d", "label": "30 دقيقة", "min_bars": 60},
}

# ✅ الفحص كل 7 دقائق
AUTO_SCAN_INTERVAL = 420
COOLDOWN           = 600   # 10 دقائق بين نفس الإشارة
FETCH_DELAY        = 2.5   # ثواني بين كل زوج
MIN_SCORE          = 8     # حد أدنى للنقاط

# ✅ الأطر للفحص التلقائي (الأهم فقط لتوفير الوقت)
AUTO_TFS = ["1د", "5د", "15د"]

last_sig  = {}
main_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ══════════════════════════════════════════════════════════════
#  Flask
# ══════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ بوت التداول يعمل"

# ══════════════════════════════════════════════════════════════
#  Telegram
# ══════════════════════════════════════════════════════════════
def tg(method, payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.json()
    except Exception as e:
        logging.error(f"tg: {e}")
        return {"ok": False}

def send(chat_id, text, kb=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb: p["reply_markup"] = kb
    for _ in range(4):
        res = tg("sendMessage", p)
        if res.get("ok"): return True
        p["parse_mode"] = ""
        time.sleep(1.5)
    return False

def edit(chat_id, mid, text, kb=None):
    p = {"chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": "HTML"}
    if kb: p["reply_markup"] = kb
    tg("editMessageText", p)

def answer(cbid):
    tg("answerCallbackQuery", {"callback_query_id": cbid})

# ══════════════════════════════════════════════════════════════
#  لوحات الأزرار — تلقائي فقط بدون يدوي
# ══════════════════════════════════════════════════════════════
def kb_main():
    return {"inline_keyboard": [
        [{"text": "🔍 فحص الكل الآن",   "callback_data": "SCANALL"}],
        [{"text": "⚙️ الإعدادات",       "callback_data": "SETTINGS"},
         {"text": "❓ مساعدة",           "callback_data": "HELP"}],
        [{"text": "📊 تحليل زوج محدد",  "callback_data": "MENU:PAIRS"}],
    ]}

def kb_pairs():
    pairs = list(SYMBOLS.keys())
    rows = []
    for i in range(0, len(pairs), 3):
        rows.append([{"text": p, "callback_data": f"P:{p}"} for p in pairs[i:i+3]])
    rows.append([{"text": "🔙 رجوع", "callback_data": "MAIN"}])
    return {"inline_keyboard": rows}

def kb_tf(pair):
    rows = []
    tfs = list(TIMEFRAMES.items())
    for i in range(0, len(tfs), 3):
        rows.append([{"text": v["label"], "callback_data": f"T:{pair}:{k}"} for k, v in tfs[i:i+3]])
    rows.append([{"text": "🔙 رجوع", "callback_data": "MENU:PAIRS"}])
    return {"inline_keyboard": rows}

def kb_back():
    return {"inline_keyboard": [
        [{"text": "🔍 فحص الكل",         "callback_data": "SCANALL"},
         {"text": "📊 تحليل زوج",        "callback_data": "MENU:PAIRS"}],
        [{"text": "🏠 القائمة الرئيسية", "callback_data": "MAIN"}],
    ]}

# ══════════════════════════════════════════════════════════════
#  جلب البيانات
# ══════════════════════════════════════════════════════════════
def fetch(ticker, interval, period, min_bars=60):
    for attempt in range(4):
        try:
            if attempt > 0:
                time.sleep(15 * attempt)

            df = yf.download(
                ticker, interval=interval, period=period,
                progress=False, auto_adjust=True, group_by="column"
            )
            if df is None or df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0].lower() for col in df.columns]
            else:
                df.columns = [col.lower() for col in df.columns]

            needed = {"open", "high", "low", "close"}
            if not needed.issubset(set(df.columns)):
                continue

            df = df[["open", "high", "low", "close"]].dropna()
            df = df[df["high"] != df["low"]]
            if len(df) < min_bars:
                continue
            return df.copy()

        except Exception as e:
            err = str(e)
            if "Too Many Requests" in err or "RateLimit" in err:
                logging.warning(f"Rate limit {ticker} — انتظار {30*attempt}s")
                time.sleep(30 * (attempt + 1))
            else:
                logging.warning(f"fetch {ticker}: {err[:60]}")

    return pd.DataFrame()

# ══════════════════════════════════════════════════════════════
#  المؤشرات الفنية المحسّنة
# ══════════════════════════════════════════════════════════════
def EMA(s, p):
    return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))

def MACD(s):
    f  = s.ewm(span=12, adjust=False).mean()
    sl = s.ewm(span=26, adjust=False).mean()
    ln = f - sl
    sg = ln.ewm(span=9, adjust=False).mean()
    return ln, sg, ln - sg

def BB(s, p=20):
    m   = s.rolling(p).mean()
    std = s.rolling(p).std(ddof=0)
    return m + 2*std, m, m - 2*std

def ATR(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()

def ADX(h, l, c, p=14):
    up   = h.diff()
    down = -l.diff()
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    tr   = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.rolling(p).mean()
    pdi  = 100 * pd.Series(pdm, index=h.index).rolling(p).mean() / (atr_ + 1e-9)
    ndi  = 100 * pd.Series(ndm, index=h.index).rolling(p).mean() / (atr_ + 1e-9)
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p).mean(), pdi, ndi

def STOCH(h, l, c, k=14, d=3):
    lk = l.rolling(k).min()
    hk = h.rolling(k).max()
    sk = 100 * (c - lk) / (hk - lk + 1e-9)
    return sk, sk.rolling(d).mean()

def VWAP(df):
    """✅ مؤشر VWAP — متوسط السعر المرجح بالحجم"""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    if "volume" in df.columns and df["volume"].sum() > 0:
        return (tp * df["volume"]).cumsum() / df["volume"].cumsum()
    return tp.rolling(20).mean()

def PIVOT(df):
    """✅ نقاط المحور — دعم ومقاومة"""
    h = float(df["high"].tail(20).max())
    l = float(df["low"].tail(20).min())
    c = float(df["close"].iloc[-1])
    pivot = (h + l + c) / 3
    r1 = 2 * pivot - l
    s1 = 2 * pivot - h
    r2 = pivot + (h - l)
    s2 = pivot - (h - l)
    return round(s2,5), round(s1,5), round(pivot,5), round(r1,5), round(r2,5)

# ══════════════════════════════════════════════════════════════
#  الشموع اليابانية — محسّنة
# ══════════════════════════════════════════════════════════════
def CANDLES(df):
    res = {"buy": [], "sell": []}
    if len(df) < 5: return res

    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values

    def body(i):  return abs(C[i] - O[i])
    def rng(i):   return H[i] - L[i] + 1e-9
    def bull(i):  return C[i] > O[i]
    def bear(i):  return C[i] < O[i]
    def uwik(i):  return H[i] - max(C[i], O[i])
    def lwik(i):  return min(C[i], O[i]) - L[i]

    i = -1

    # ── صعودية ──
    # مطرقة
    if bull(i) and body(i) > 0 and lwik(i) >= 2*body(i) and uwik(i) <= body(i)*0.3:
        res["buy"].append("🔨 مطرقة")
    # ابتلاع صعودي
    if bear(-2) and bull(i) and O[i] <= C[-2] and C[i] >= O[-2] and body(i) > body(-2):
        res["buy"].append("🕯 ابتلاع↑")
    # بين بار صعودي
    if body(i) > 0 and lwik(i) >= 2.5*body(i) and uwik(i) <= body(i)*0.4:
        res["buy"].append("📌 بين بار↑")
    # 3 جنود بيض
    if (bull(-3) and bull(-2) and bull(i) and
        C[i]>C[-2]>C[-3] and O[i]>O[-2]>O[-3] and
        body(i)>0 and body(-2)>0 and body(-3)>0):
        res["buy"].append("⚔️ 3جنود")
    # نجمة الصباح
    if (bear(-3) and body(-2) < rng(-2)*0.25 and
        bull(i) and C[i] > (O[-3]+C[-3])/2):
        res["buy"].append("⭐ نجمة الصباح")
    # Tweezer Bottom
    if bear(-2) and bull(i) and abs(L[i]-L[-2]) < rng(i)*0.05:
        res["buy"].append("🔧 قاع توأم")
    # Doji صعودي (بعد هبوط)
    if body(i) < rng(i)*0.08 and bear(-2) and bear(-3):
        res["buy"].append("⚖️ دوجي↑")

    # ── هبوطية ──
    # نجمة ساقطة
    if bear(i) and body(i) > 0 and uwik(i) >= 2*body(i) and lwik(i) <= body(i)*0.3:
        res["sell"].append("💫 نجمة ساقطة")
    # ابتلاع هبوطي
    if bull(-2) and bear(i) and O[i] >= C[-2] and C[i] <= O[-2] and body(i) > body(-2):
        res["sell"].append("🕯 ابتلاع↓")
    # بين بار هبوطي
    if body(i) > 0 and uwik(i) >= 2.5*body(i) and lwik(i) <= body(i)*0.4:
        res["sell"].append("📌 بين بار↓")
    # 3 غربان سود
    if (bear(-3) and bear(-2) and bear(i) and
        C[i]<C[-2]<C[-3] and O[i]<O[-2]<O[-3] and
        body(i)>0 and body(-2)>0 and body(-3)>0):
        res["sell"].append("🦅 3غربان")
    # نجمة المساء
    if (bull(-3) and body(-2) < rng(-2)*0.25 and
        bear(i) and C[i] < (O[-3]+C[-3])/2):
        res["sell"].append("🌙 نجمة المساء")
    # Tweezer Top
    if bull(-2) and bear(i) and abs(H[i]-H[-2]) < rng(i)*0.05:
        res["sell"].append("🔧 قمة توأم")

    return res

# ══════════════════════════════════════════════════════════════
#  محرك التحليل المحسّن
# ══════════════════════════════════════════════════════════════
def analyze(df):
    if df is None or df.empty or len(df) < 50:
        return None, 0, {}

    cl = df["close"].astype(float)
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)

    # ── المؤشرات ──
    e8   = float(EMA(cl, 8).iloc[-1])
    e21  = float(EMA(cl, 21).iloc[-1])
    e50  = float(EMA(cl, 50).iloc[-1])
    e200 = float(EMA(cl, 200).iloc[-1]) if len(cl) >= 200 else e50

    rv    = float(RSI(cl).iloc[-1])
    rv_p  = float(RSI(cl).iloc[-2])  # RSI السابق

    ml, ms, mh = MACD(cl)
    mlv  = float(ml.iloc[-1]); msv = float(ms.iloc[-1])
    mlp  = float(ml.iloc[-2]); msp = float(ms.iloc[-2])
    mhv  = float(mh.iloc[-1]); mhp = float(mh.iloc[-2])

    bu, bm, bl = BB(cl)
    buv  = float(bu.iloc[-1]); blv = float(bl.iloc[-1]); bmv = float(bm.iloc[-1])

    av   = max(float(ATR(hi, lo, cl).iloc[-1]), 0.00005)

    sk, sd = STOCH(hi, lo, cl)
    skv  = float(sk.iloc[-1]); sdv = float(sd.iloc[-1])
    skp  = float(sk.iloc[-2]); sdp = float(sd.iloc[-2])

    adxs, pdis, ndis = ADX(hi, lo, cl)
    adxv = float(adxs.iloc[-1])
    pdiv = float(pdis.iloc[-1])
    ndiv = float(ndis.iloc[-1])

    s2, s1, pvt, r1, r2 = PIVOT(df)
    p = float(cl.iloc[-1])

    # BB width — ضيق = breakout قريب
    bb_width = (buv - blv) / (bmv + 1e-9)

    buy = sell = 0
    rb = []; rs = []

    # ── 1. EMA (4 نقاط) ──
    if e8 > e21 > e50:
        buy += 4; rb.append("EMA صاعد")
        if e50 > e200: buy += 1; rb.append("فوق EMA200")
    elif e8 < e21 < e50:
        sell += 4; rs.append("EMA هابط")
        if e50 < e200: sell += 1; rs.append("تحت EMA200")

    # ── 2. RSI مع اتجاه (3 نقاط) ──
    if rv < 30:
        buy += 3; rb.append(f"RSI ذروة بيع {rv:.0f}")
    elif rv < 45 and rv > rv_p:
        buy += 2; rb.append(f"RSI صاعد {rv:.0f}")
    elif rv > 70:
        sell += 3; rs.append(f"RSI ذروة شراء {rv:.0f}")
    elif rv > 55 and rv < rv_p:
        sell += 2; rs.append(f"RSI هابط {rv:.0f}")

    # ── 3. MACD (3 نقاط للتقاطع) ──
    if mlp < msp and mlv > msv:
        buy += 3; rb.append("MACD تقاطع↑")
    elif mlp > msp and mlv < msv:
        sell += 3; rs.append("MACD تقاطع↓")
    elif mhv > 0 and mhv > mhp:
        buy += 1; rb.append("MACD↑")
    elif mhv < 0 and mhv < mhp:
        sell += 1; rs.append("MACD↓")

    # ── 4. Bollinger Bands (2-3 نقاط) ──
    if p <= blv:
        buy += 3; rb.append("BB حد سفلي")
    elif p < bmv and bb_width > 0.005:
        buy += 1; rb.append("BB منتصف↓")
    elif p >= buv:
        sell += 3; rs.append("BB حد علوي")
    elif p > bmv and bb_width > 0.005:
        sell += 1; rs.append("BB منتصف↑")

    # ── 5. Stochastic (2 نقاط) ──
    if skv < 20 and sdv < 20:
        buy += 2; rb.append(f"Stoch تشبع بيع {skv:.0f}")
    elif skv > 80 and sdv > 80:
        sell += 2; rs.append(f"Stoch تشبع شراء {skv:.0f}")
    # تقاطع stoch
    if skp < sdp and skv > sdv and skv < 50:
        buy += 2; rb.append("Stoch تقاطع↑")
    elif skp > sdp and skv < sdv and skv > 50:
        sell += 2; rs.append("Stoch تقاطع↓")

    # ── 6. ADX — قوة الاتجاه (2 نقاط) ──
    if adxv > 20:
        if pdiv > ndiv:
            buy += 2; rb.append(f"ADX↑ {adxv:.0f}")
        elif ndiv > pdiv:
            sell += 2; rs.append(f"ADX↓ {adxv:.0f}")

    # ── 7. Pivot Points (2 نقاط) ──
    if abs(p - s1) < av * 1.5:
        buy += 2; rb.append(f"دعم S1={s1}")
    elif abs(p - s2) < av * 1.5:
        buy += 2; rb.append(f"دعم S2={s2}")
    elif abs(p - r1) < av * 1.5:
        sell += 2; rs.append(f"مقاومة R1={r1}")
    elif abs(p - r2) < av * 1.5:
        sell += 2; rs.append(f"مقاومة R2={r2}")

    # ── 8. الشموع اليابانية ──
    cds = CANDLES(df)
    weights = {
        "🔨 مطرقة": 3, "🕯 ابتلاع↑": 4, "📌 بين بار↑": 3,
        "⚔️ 3جنود": 5, "⭐ نجمة الصباح": 4, "🔧 قاع توأم": 3, "⚖️ دوجي↑": 1,
        "💫 نجمة ساقطة": 3, "🕯 ابتلاع↓": 4, "📌 بين بار↓": 3,
        "🦅 3غربان": 5, "🌙 نجمة المساء": 4, "🔧 قمة توأم": 3,
    }
    for nm in cds["buy"]:
        buy += weights.get(nm, 2); rb.append(nm)
    for nm in cds["sell"]:
        sell += weights.get(nm, 2); rs.append(nm)

    # ══ فلاتر ذكية ══
    # لا شراء إذا EMA هابط تماماً
    if buy > sell and e8 < e21 and e21 < e50: return None, 0, {}
    # لا بيع إذا EMA صاعد تماماً
    if sell > buy and e8 > e21 and e21 > e50: return None, 0, {}
    # لا شراء في ذروة الشراء الشديدة
    if buy > sell and rv > 80: return None, 0, {}
    # لا بيع في ذروة البيع الشديدة
    if sell > buy and rv < 20: return None, 0, {}
    # لا إشارة بدون اتجاه ADX واضح إذا النقاط ضعيفة
    if max(buy, sell) < 10 and adxv < 15: return None, 0, {}

    score = max(buy, sell)
    if score < MIN_SCORE: return None, 0, {}

    total = buy + sell
    conf  = int((score / total) * 100) if total else 0

    # ✅ نجوم الجودة
    if score >= 20:   stars = "⭐⭐⭐⭐⭐"
    elif score >= 16: stars = "⭐⭐⭐⭐"
    elif score >= 13: stars = "⭐⭐⭐"
    elif score >= 10: stars = "⭐⭐"
    else:             stars = "⭐"

    # ✅ وقف الخسارة وهدف الربح بناءً على ATR وقوة الإشارة
    sl_mult = 1.2 if score >= 15 else 1.5
    tp_mult = 2.5 if score >= 15 else 2.0

    det = {
        "price": round(p, 5),
        "rsi":   round(rv, 1),
        "adx":   round(adxv, 1),
        "atr":   round(av, 5),
        "bbl":   round(blv, 5),
        "bbu":   round(buv, 5),
        "pvt":   pvt, "r1": r1, "s1": s1,
        "sl_b":  round(p - sl_mult * av, 5),
        "tp_b":  round(p + tp_mult * av, 5),
        "sl_s":  round(p + sl_mult * av, 5),
        "tp_s":  round(p - tp_mult * av, 5),
        "rr":    f"1 : {tp_mult/sl_mult:.2f}",
        "stars": stars, "score": score, "conf": conf,
    }

    if buy > sell:
        det["cds"] = " | ".join(cds["buy"]) if cds["buy"] else "—"
        det["why"] = " | ".join(rb)
        return "🟢 شراء", score, det
    if sell > buy:
        det["cds"] = " | ".join(cds["sell"]) if cds["sell"] else "—"
        det["why"] = " | ".join(rs)
        return "🔴 بيع", score, det

    return None, 0, {}

# ══════════════════════════════════════════════════════════════
#  تنسيق الرسالة
# ══════════════════════════════════════════════════════════════
def fmt(name, tf_label, sig, det):
    is_buy = "شراء" in sig
    sl = det["sl_b"] if is_buy else det["sl_s"]
    tp = det["tp_b"] if is_buy else det["tp_s"]
    arrow = "📈" if is_buy else "📉"
    return (
        f"{arrow} <b>{name}</b> | <i>{tf_label}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if is_buy else '🔴'} <b>{sig}</b>  {det['stars']}\n"
        f"🎯 الثقة: <b>{det['conf']}%</b> | نقاط: <code>{det['score']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 السعر:        <code>{det['price']}</code>\n"
        f"🛑 وقف الخسارة: <code>{sl}</code>\n"
        f"✅ هدف الربح:   <code>{tp}</code>\n"
        f"⚖️ نسبة R:R:    <code>{det['rr']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 RSI: <code>{det['rsi']}</code> | ADX: <code>{det['adx']}</code>\n"
        f"📊 BB: <code>{det['bbl']}</code> ↔ <code>{det['bbu']}</code>\n"
        f"🏛 Pivot: <code>{det['pvt']}</code> | S1: <code>{det['s1']}</code> | R1: <code>{det['r1']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕯 شموع: <i>{det['cds']}</i>\n"
        f"📋 أسباب: <i>{det['why']}</i>\n"
        f"🕒 <i>{datetime.now().strftime('%H:%M:%S')}</i>"
    )

# ══════════════════════════════════════════════════════════════
#  الفحص التلقائي
# ══════════════════════════════════════════════════════════════
def do_scan(chat_id=None, pair=None, tfk=None):
    found = False

    if pair and tfk:
        # تحليل زوج محدد بإطار محدد
        pairs_to_scan = {pair: SYMBOLS[pair]}
        tfs_to_scan   = {tfk: TIMEFRAMES[tfk]}
    elif pair:
        # تحليل زوج محدد بكل الأطر
        pairs_to_scan = {pair: SYMBOLS[pair]}
        tfs_to_scan   = TIMEFRAMES
    else:
        # فحص كل الأزواج
        pairs_to_scan = SYMBOLS
        tfs_to_scan   = {k: TIMEFRAMES[k] for k in AUTO_TFS}

    for name, ticker in pairs_to_scan.items():
        time.sleep(FETCH_DELAY)
        for tk, tcfg in tfs_to_scan.items():
            df = fetch(ticker, tcfg["interval"], tcfg["period"], tcfg["min_bars"])
            sig, score, det = analyze(df)
            if not sig: continue

            if chat_id is None:
                key = f"{name}|{tk}|{sig}"
                with main_lock:
                    if time.time() - last_sig.get(key, 0) < COOLDOWN: continue
                    last_sig[key] = time.time()

            found = True
            tgt = chat_id if chat_id else CHAT_ID
            send(tgt, fmt(name, tcfg["label"], sig, det), kb_back())
            time.sleep(1)

    return found

def auto_scan():
    time.sleep(15)
    while True:
        try:
            logging.info("🔍 فحص تلقائي...")
            do_scan()
        except Exception as e:
            logging.error(f"auto_scan: {e}")
        time.sleep(AUTO_SCAN_INTERVAL)

# ══════════════════════════════════════════════════════════════
#  معالجة الأزرار
# ══════════════════════════════════════════════════════════════
def on_cb(chat_id, mid, cbid, data):
    answer(cbid)

    if data == "MAIN":
        edit(chat_id, mid,
            "🤖 <b>بوت التداول الاحترافي</b>\n\n"
            "🔄 يفحص تلقائياً كل 7 دقائق\n"
            "📊 16 زوج | 6 أطر زمنية",
            kb_main())

    elif data == "SCANALL":
        edit(chat_id, mid, "🔍 <b>جاري فحص كل الأزواج...</b>\n<i>قد يستغرق دقيقتين</i>")
        found = do_scan(chat_id=chat_id)
        if not found:
            send(chat_id,
                "⚪ <b>لا توجد إشارات مؤكدة الآن</b>\n"
                "<i>البوت يراقب تلقائياً كل 7 دقائق</i>",
                kb_main())

    elif data == "SETTINGS":
        edit(chat_id, mid,
            f"⚙️ <b>الإعدادات الحالية:</b>\n\n"
            f"⏱ الفحص التلقائي: كل <code>{AUTO_SCAN_INTERVAL//60}</code> دقائق\n"
            f"🎯 الحد الأدنى للنقاط: <code>{MIN_SCORE}</code>\n"
            f"🔕 كولداون: <code>{COOLDOWN//60}</code> دقائق\n"
            f"📊 الأزواج: <code>{len(SYMBOLS)}</code>\n"
            f"⏰ الأطر التلقائية: <code>{', '.join(AUTO_TFS)}</code>",
            kb_main())

    elif data == "HELP":
        edit(chat_id, mid,
            "❓ <b>كيف يعمل البوت:</b>\n\n"
            "🔄 <b>تلقائي:</b> يفحص كل 7 دقائق ويرسل إشارات مباشرة\n\n"
            "📊 <b>يدوي:</b> اضغط تحليل زوج محدد\n\n"
            "<b>المؤشرات المستخدمة:</b>\n"
            "• EMA 8/21/50/200\n"
            "• RSI مع اتجاه\n"
            "• MACD تقاطعات\n"
            "• Bollinger Bands\n"
            "• Stochastic\n"
            "• ADX قوة الاتجاه\n"
            "• Pivot Points دعم/مقاومة\n"
            "• 13 نمط شمعة يابانية\n\n"
            "⭐ = ضعيف | ⭐⭐⭐⭐⭐ = قوي جداً\n\n"
            "<b>نصيحة:</b> تداول فقط على ⭐⭐⭐ وما فوق",
            kb_main())

    elif data == "MENU:PAIRS":
        edit(chat_id, mid, "📊 <b>اختر زوج العملات:</b>", kb_pairs())

    elif data.startswith("P:"):
        pair = data[2:]
        edit(chat_id, mid, f"⏱ <b>اختر الإطار الزمني لـ {pair}:</b>", kb_tf(pair))

    elif data.startswith("T:"):
        _, pair, tfk = data.split(":", 2)
        edit(chat_id, mid, f"🔍 <b>جاري تحليل {pair} | {TIMEFRAMES[tfk]['label']}...</b>")
        ticker = SYMBOLS.get(pair)
        tcfg   = TIMEFRAMES.get(tfk)
        if not ticker or not tcfg:
            send(chat_id, "❌ خطأ", kb_main())
            return
        df = fetch(ticker, tcfg["interval"], tcfg["period"], tcfg["min_bars"])
        sig, score, det = analyze(df)
        if sig:
            send(chat_id, fmt(pair, tcfg["label"], sig, det), kb_back())
        else:
            send(chat_id,
                f"📊 <b>{pair}</b> | {tcfg['label']}\n\n"
                f"⚪ لا توجد إشارة مؤكدة الآن\n"
                f"<i>جرب إطاراً زمنياً آخر أو انتظر الفحص التلقائي</i>",
                kb_back())

# ══════════════════════════════════════════════════════════════
#  الأوامر
# ══════════════════════════════════════════════════════════════
def on_cmd(chat_id, txt):
    if txt.strip() in ["/start", "/menu"]:
        send(chat_id,
            "🤖 <b>بوت التداول الاحترافي</b>\n\n"
            "✅ فحص تلقائي كل 7 دقائق\n"
            "✅ 16 زوج عملات + ذهب\n"
            "✅ 6 أطر زمنية (1د إلى 30د)\n"
            "✅ 7 مؤشرات + 13 نمط شمعة\n"
            "✅ وقف خسارة وهدف ربح تلقائي\n\n"
            "اختر من القائمة:",
            kb_main())

# ══════════════════════════════════════════════════════════════
#  Polling
# ══════════════════════════════════════════════════════════════
def polling():
    last_id = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
                f"?offset={last_id+1}&timeout=10",
                timeout=15
            ).json()
            for upd in res.get("result", []):
                last_id = upd["update_id"]
                if "message" in upd:
                    msg  = upd["message"]
                    chat = msg.get("chat", {}).get("id")
                    txt  = msg.get("text", "")
                    if chat and txt:
                        threading.Thread(target=on_cmd, args=(chat, txt), daemon=True).start()
                elif "callback_query" in upd:
                    cb   = upd["callback_query"]
                    chat = cb["message"]["chat"]["id"]
                    mid  = cb["message"]["message_id"]
                    cbid = cb["id"]
                    data = cb.get("data", "")
                    threading.Thread(target=on_cb, args=(chat, mid, cbid, data), daemon=True).start()
        except Exception as e:
            logging.error(f"polling: {e}")
            time.sleep(3)

# ══════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    threading.Thread(target=auto_scan, daemon=True).start()
    threading.Thread(target=polling,   daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"✅ البوت اشتغل على port {port}")
    try:
        from waitress import serve
        logging.info("✅ waitress production server")
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
