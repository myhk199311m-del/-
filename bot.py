import os
import time
import json
import csv
import threading
import logging
from datetime import datetime

import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 85

SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "CAD/CHF": "CADCHF=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "GBP/AUD": "GBPAUD=X", "XAU/USD": "GC=F",
}

TIMEFRAMES = {
    "1د":  {"interval": "1m",  "period": "1d",  "label": "1 دقيقة",  "bars": 50, "exp": "01:00"},
    "5د":  {"interval": "5m",  "period": "5d",  "label": "5 دقائق",  "bars": 60, "exp": "05:00"},
    "15د": {"interval": "15m", "period": "10d", "label": "15 دقيقة", "bars": 60, "exp": "15:00"},
    "30د": {"interval": "30m", "period": "15d", "label": "30 دقيقة", "bars": 60, "exp": "30:00"},
    "1س":  {"interval": "60m", "period": "30d", "label": "ساعة",     "bars": 60, "exp": "01:00:00"},
}

MAIN_TF = "5د"
CONFIRM_TFS = ["15د"]
MIN_CONFIRM = 1
MIN_SCORE = 5
ADX_THRESHOLD = 25
COOLDOWN = 300
FETCH_DELAY = 2.0

LOG_FILE = "signals_log.csv"
last_sig = {}
lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
app = Flask(__name__)

def tg(method, data):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        return requests.post(url, json=data, timeout=20).json()
    except Exception as e:
        logging.warning(f"Telegram Error: {e}")
        return {"ok": False}

def send(cid, txt, kb=None):
    p = {"chat_id": cid, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb:
        p["reply_markup"] = kb
    for _ in range(3):
        res = tg("sendMessage", p)
        if res.get("ok"):
            return res
        time.sleep(1)
    return {"ok": False}

def edit(cid, mid, txt, kb=None):
    p = {"chat_id": cid, "message_id": mid, "text": txt, "parse_mode": "HTML"}
    if kb:
        p["reply_markup"] = kb
    return tg("editMessageText", p)

def answer(cbid):
    tg("answerCallbackQuery", {"callback_query_id": cbid})

def otc(name):
    return f"{name} OTC"

def kb_main():
    return {"inline_keyboard": [
        [{"text": "⚡ فحص الكل", "callback_data": "SCANALL"}],
        [{"text": "📊 اختر زوج", "callback_data": "PAIRS"}, {"text": "🏆 أقوى إشارة", "callback_data": "BEST"}],
        [{"text": "📈 تقرير الأداء", "callback_data": "REPORT"}, {"text": "💰 حاسبة المخاطرة", "callback_data": "RISK"}],
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

def fetch(ticker, interval, period, min_bars=50):
    try:
        df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return pd.DataFrame()
        df = df[["open", "high", "low", "close"]].dropna()
        df = df[df["high"] != df["low"]]
        if len(df) < min_bars:
            return pd.DataFrame()
        return df.copy()
    except Exception as e:
        logging.warning(f"Fetch {ticker}: {e}")
        return pd.DataFrame()

def EMA(s, p):
    return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/(l + 1e-9))

def MACD(s):
    f = s.ewm(span=12, adjust=False).mean()
    sl = s.ewm(span=26, adjust=False).mean()
    ln = f - sl
    return ln, ln.ewm(span=9, adjust=False).mean()

def BB(s, p=20):
    m = s.rolling(p).mean()
    std = s.rolling(p).std(ddof=0)
    return m + 2*std, m, m - 2*std

def ATR(h, l, c, p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()

def ADX(h, l, c, p=14):
    up = h.diff()
    down = -l.diff()
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p, min_periods=1).mean().replace(0, 1e-9)
    pdi = 100 * pd.Series(pdm, index=h.index).rolling(p).mean() / atr
    ndi = 100 * pd.Series(ndm, index=h.index).rolling(p).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p, min_periods=1).mean(), pdi, ndi

def STOCH(h, l, c, k=14):
    ll = l.rolling(k, min_periods=1).min()
    hh = h.rolling(k, min_periods=1).max()
    return 100 * (c - ll) / (hh - ll + 1e-9)

def PIVOT(df):
    h = float(df["high"].tail(20).max())
    l = float(df["low"].tail(20).min())
    c = float(df["close"].iloc[-1])
    pv = (h + l + c) / 3
    return round(pv - (h-l), 5), round(2*pv - h, 5), round(pv, 5), round(2*pv - l, 5), round(pv + (h-l), 5)

def CANDLES(df):
    res = {"buy": [], "sell": []}
    if len(df) < 5:
        return res
    O, H, L, C = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    def body(i): return abs(C[i] - O[i])
    def bull(i): return C[i] > O[i]
    def bear(i): return C[i] < O[i]
    i = -1
    if bull(i) and body(i) > 0 and (min(C[i], O[i]) - L[i]) >= 1.5 * body(i):
        res["buy"].append("Pin Bar ↑")
    if bear(-2) and bull(i) and body(i) > body(-2) * 0.8:
        res["buy"].append("Engulfing ↑")
    if bear(i) and body(i) > 0 and (H[i] - max(C[i], O[i])) >= 1.5 * body(i):
        res["sell"].append("Pin Bar ↓")
    if bull(-2) and bear(i) and body(i) > body(-2) * 0.8:
        res["sell"].append("Engulfing ↓")
    return res

def analyze(df):
    if df is None or df.empty or len(df) < 30:
        return None, 0, {}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    e8 = float(EMA(close, 8).iloc[-1])
    e21 = float(EMA(close, 21).iloc[-1])
    rsi = float(RSI(close, 14).iloc[-1])
    macd, sig_macd = MACD(close)
    bb_u, bb_m, bb_l = BB(close)
    atr_val = max(float(ATR(high, low, close).iloc[-1]), 1e-5)
    stoch_k = float(STOCH(high, low, close).iloc[-1])
    adx, plus_di, minus_di = ADX(high, low, close)
    adx_val = float(adx.iloc[-1])
    s2, s1, pivot, r1, r2 = PIVOT(df)
    price = float(close.iloc[-1])

    buy = 0
    sell = 0
    rb = []
    rs = []

    if adx_val < ADX_THRESHOLD:
        return None, 0, {}

    if e8 > e21:
        buy += 3
        rb.append("EMA صاعد")
    elif e8 < e21:
        sell += 3
        rs.append("EMA هابط")

    if rsi < 35:
        buy += 3
        rb.append(f"RSI {rsi:.0f} تشبع بيع")
    elif rsi > 65:
        sell += 3
        rs.append(f"RSI {rsi:.0f} تشبع شراء")

    if macd.iloc[-2] < sig_macd.iloc[-2] and macd.iloc[-1] > sig_macd.iloc[-1]:
        buy += 3
        rb.append("MACD تقاطع صاعد")
    if macd.iloc[-2] > sig_macd.iloc[-2] and macd.iloc[-1] < sig_macd.iloc[-1]:
        sell += 3
        rs.append("MACD تقاطع هابط")

    if price <= bb_l.iloc[-1] * 1.01:
        buy += 2
        rb.append("دعم بولينجر")
    if price >= bb_u.iloc[-1] * 0.99:
        sell += 2
        rs.append("مقاومة بولينجر")

    if stoch_k < 25:
        buy += 2
        rb.append("ستوكاستيك منخفض")
    if stoch_k > 75:
        sell += 2
        rs.append("ستوكاستيك مرتفع")

    if abs(price - s1) < atr_val * 1.2:
        buy += 2
        rb.append(f"قريب من دعم S1={s1}")
    if abs(price - r1) < atr_val * 1.2:
        sell += 2
        rs.append(f"قريب من مقاومة R1={r1}")

    patterns = CANDLES(df)
    weights = {"Pin Bar ↑": 2, "Engulfing ↑": 3, "Pin Bar ↓": 2, "Engulfing ↓": 3}
    for p in patterns["buy"]:
        buy += weights.get(p, 1)
        rb.append(p)
    for p in patterns["sell"]:
        sell += weights.get(p, 1)
        rs.append(p)

    if buy > sell and rsi > 90:
        return None, 0, {}
    if sell > buy and rsi < 10:
        return None, 0, {}

    score = max(buy, sell)
    if score < MIN_SCORE:
        return None, 0, {}

    total = buy + sell
    conf = int((score / total) * 100) if total else 50
    power = min(100, int(score * 3))

    stake = round(BALANCE * (RISK_PCT / 100), 2)
    profit = round(stake * (PAYOUT / 100), 2)

    det = {
        "price": round(price, 5), "rsi": round(rsi, 1), "adx": round(adx_val, 1),
        "atr": round(atr_val, 5), "s1": s1, "r1": r1, "pivot": pivot,
        "sl_b": round(price - 1.5 * atr_val, 5), "tp_b": round(price + 2.5 * atr_val, 5),
        "sl_s": round(price + 1.5 * atr_val, 5), "tp_s": round(price - 2.5 * atr_val, 5),
        "conf": conf, "power": power, "stake": stake, "profit": profit,
        "why": " | ".join(rb if buy > sell else rs)
    }

    return ("BUY", score, det) if buy > sell else ("SELL", score, det)

def get_multi_timeframe_signal(pair):
    ticker = SYMBOLS[pair]
    main_cfg = TIMEFRAMES[MAIN_TF]
    df_main = fetch(ticker, main_cfg["interval"], main_cfg["period"], main_cfg["bars"])
    sig_main, score_main, det_main = analyze(df_main)

    if not sig_main or score_main < MIN_SCORE:
        return None, 0, {}, 0

    confirm_count = 0
    confirm_list = []
    for tf in CONFIRM_TFS:
        cfg = TIMEFRAMES[tf]
        df_tf = fetch(ticker, cfg["interval"], cfg["period"], cfg["bars"])
        sig_tf, score_tf, _ = analyze(df_tf)
        if sig_tf == sig_main and score_tf >= MIN_SCORE - 1:
            confirm_count += 1
            confirm_list.append(cfg["label"])

    if confirm_count >= MIN_CONFIRM:
        det_main["confirm"] = f"✅ مؤكد من: {', '.join(confirm_list)}"
        return sig_main, score_main + 2, det_main, confirm_count
    return None, 0, {}, 0

def log_signal(pair, sig, score, power, conf, tf, exp):
    exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["التاريخ", "الزوج", "الإشارة", "النقاط", "القوة", "الثقة", "الإطار", "المدة"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), pair, sig, score, power, conf, tf, exp])

def get_report():
    if not os.path.isfile(LOG_FILE):
        return "📈 لا يوجد سجل إشارات حتى الآن"
    try:
        df = pd.read_csv(LOG_FILE)
        total = len(df)
        buy = len(df[df["الإشارة"] == "BUY"])
        sell = len(df[df["الإشارة"] == "SELL"])
        avg_p = round(df["القوة"].mean(), 1)
        avg_c = round(df["الثقة"].mean(), 1)
        return (
            f"📊 <b>تقرير الأداء</b>
"
            f"━━━━━━━━━━━━━━━━━━━━
"
            f"إجمالي: {total} | شراء: {buy} | بيع: {sell}
"
            f"متوسط القوة: {avg_p}/100 | الثقة: {avg_c}%
"
        )
    except Exception:
        return "📈 سجل فارغ"

def format_signal(pair, tf, sig, det, exp):
    arrow = "▲" if sig == "BUY" else "▼"
    color = "🟢" if sig == "BUY" else "🔴"
    sl = det["sl_b"] if sig == "BUY" else det["sl_s"]
    tp = det["tp_b"] if sig == "BUY" else det["tp_s"]
    confirm = det.get("confirm", "")
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━
"
        f"⚡ <b>BASILISK v2.3</b> • {otc(pair)}
"
        f"⏱ {tf} | {exp}
{confirm}
"
        "━━━━━━━━━━━━━━━━━━━━━━━━
"
        f"{arrow} {color} <b>{sig}</b> {arrow}
"
        "━━━━━━━━━━━━━━━━━━━━━━━━
"
        f"💪 القوة: <code>{det['power']}/100</code> | الثقة: <code>{det['conf']}%</code>
"
        f"💰 السعر: <code>{det['price']}</code>
"
        f"📊 ADX: <code>{det['adx']}</code> | RSI: <code>{det['rsi']}</code>
"
        f"🛑 إيقاف الخسارة: <code>{sl}</code> | الهدف: <code>{tp}</code>
"
        f"💵 المخاطرة: <code>${det['stake']}</code> | الربح: <code>+${det['profit']}</code>
"
        "━━━━━━━━━━━━━━━━━━━━━━━━
"
        f"📋 السبب: <i>{det['why']}</i>
"
        f"🕒 {datetime.now().strftime('%H:%M:%S')}
"
        "⚠️ ليس توصية تداول
"
    )

def format_no_signal(pair, tf):
    return (
        "━━━━━━━━━━━━━━━━━━━━
"
        f"⚡ {otc(pair)} | {tf}
"
        "⚠️ لا توجد إشارة واضحة حالياً
"
        "━━━━━━━━━━━━━━━━━━━━"
    )

def do_scan(chat_id=None, pair=None, tfk=None):
    tgt = chat_id or CHAT_ID
    if pair and tfk:
        cfg = TIMEFRAMES[tfk]
        df = fetch(SYMBOLS[pair], cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)
        if sig:
            send(tgt, format_signal(pair, cfg["label"], sig, det, cfg["exp"]), kb_result())
            log_signal(pair, sig, score, det["power"], det["conf"], cfg["label"], cfg["exp"])
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
        send(tgt, "⚠️ لا توجد إشارات مؤكدة حالياً", kb_main())
        return

    for name, tf, sig, score, det, exp in found:
        send(tgt, format_signal(name, tf, sig, det, exp), kb_result())
        log_signal(name, sig, score, det["power"], det["conf"], tf, exp)
        time.sleep(1)

def on_command(cid, txt):
    if txt.strip() in ["/start", "/menu"]:
        send(
            cid,
            "⚡ <b>BASILISK v2.3 • النسخة المحسّنة</b>

"
            "✅ يدوي بالكامل
"
            "✅ فلتر ADX للاتجاه
"
            "✅ تحليل متعدد الأطر الزمنية
"
            "✅ دعم ومقاومة ونقاط محورية
"
            "✅ منطق نقاط محسّن

"
            "اختر من القائمة:",
            kb_main()
        )

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
        s = round(BALANCE * (RISK_PCT / 100), 2)
        p = round(s * (PAYOUT / 100), 2)
        edit(cid, mid, f"💰 الرصيد: ${BALANCE}
المخاطرة {RISK_PCT}% = ${s}
ربح محتمل: +${p}", kb_main())
    elif data == "HELP":
        edit(cid, mid, "📌 استخدم فحص الكل أو اختر زوج ثم إطار زمني للحصول على إشارة.", kb_main())
    elif data.startswith("P:"):
        edit(cid, mid, f"⏱ اختر الإطار لـ {otc(data[2:])}", kb_tf(data[2:]))
    elif data.startswith("T:"):
        _, pr, tf = data.split(":")
        edit(cid, mid, f"🔍 جاري تحليل {otc(pr)}...")
        threading.Thread(target=do_scan, args=(cid, pr, tf), daemon=True).start()

def polling():
    last = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": last + 1, "timeout": 30, "allowed_updates": json.dumps(["message", "callback_query"])},
                timeout=35,
            ).json()
            for upd in res.get("result", []):
                last = upd["update_id"]
                if "message" in upd:
                    msg = upd["message"]
                    on_command(msg["chat"]["id"], msg.get("text", ""))
                if "callback_query" in upd:
                    cb = upd["callback_query"]
                    on_callback(cb["message"]["chat"]["id"], cb["message"]["message_id"], cb["id"], cb["data"])
        except Exception as e:
            logging.error(f"Polling: {e}")
            time.sleep(3)

@app.route("/")
def home():
    return "✅ BASILISK v2.3 • RUNNING"

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ أدخل BOT_TOKEN أولاً")
    else:
        threading.Thread(target=polling, daemon=True).start()
        port = int(os.environ.get("PORT", 10000))
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port)
        except Exception:
            app.run(host="0.0.0.0", port=port)
