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
# ⚙️ الإعدادات العامة - سهلة التعديل
# ==============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 85

# قائمة أزواج العملات
SYMBOLS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CAD": "USDCAD=X",
    "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "AUD/CAD": "AUDCAD=X",
    "AUD/CHF": "AUDCHF=X",
    "CAD/CHF": "CADCHF=X",
    "CAD/JPY": "CADJPY=X",
    "CHF/JPY": "CHFJPY=X",
    "GBP/AUD": "GBPAUD=X",
    "XAU/USD": "GC=F",
}

# الأطر الزمنية
TIMEFRAMES = {
    "1د":  {"interval": "1m",  "period": "1d",  "label": "1 دقيقة",  "bars": 50, "exp": "01:00"},
    "5د":  {"interval": "5m",  "period": "5d",  "label": "5 دقائق",  "bars": 60, "exp": "05:00"},
    "15د": {"interval": "15m", "period": "10d", "label": "15 دقيقة", "bars": 60, "exp": "15:00"},
    "30د": {"interval": "30m", "period": "15d", "label": "30 دقيقة", "bars": 60, "exp": "30:00"},
    "1س":  {"interval": "60m", "period": "30d", "label": "ساعة",     "bars": 60, "exp": "01:00:00"},
}

# إعدادات التحليل
MAIN_TF = "5د"
CONFIRM_TFS = []
MIN_CONFIRM = 0
MIN_SCORE = 4
COOLDOWN = 300
FETCH_DELAY = 2.0

# ملف السجلات
LOG_FILE = "signals_log.csv"
last_sig = {}
lock = threading.Lock()

# تسجيل الأحداث
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
app = Flask(__name__)

# ==============================================
# 📡 دوال الاتصال بـ Telegram
# ==============================================
def tg(method, data):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        return requests.post(url, json=data, timeout=20).json()
    except Exception as e:
        logging.warning(f"Telegram API Error: {e}")
        return {"ok": False}

def send(cid, txt, kb=None):
    p = {
        "chat_id": cid,
        "text": txt,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if kb:
        p["reply_markup"] = kb
    for _ in range(3):
        res = tg("sendMessage", p)
        if res.get("ok"):
            return res
        time.sleep(1)
    return {"ok": False}

def edit(cid, mid, txt, kb=None):
    p = {
        "chat_id": cid,
        "message_id": mid,
        "text": txt,
        "parse_mode": "HTML"
    }
    if kb:
        p["reply_markup"] = kb
    return tg("editMessageText", p)

def answer(cbid):
    tg("answerCallbackQuery", {"callback_query_id": cbid})

# ==============================================
# 🎛️ واجهة الأزرار
# ==============================================
def otc(name):
    return f"{name} OTC"

def kb_main():
    return {"inline_keyboard": [
        [{"text": "⚡ فحص الكل", "callback_data": "SCANALL"}],
        [{"text": "📊 اختر زوج", "callback_data": "PAIRS"},
         {"text": "🏆 أقوى إشارة", "callback_data": "BEST"}],
        [{"text": "📈 تقرير الأداء", "callback_data": "REPORT"},
         {"text": "💰 حاسبة المخاطرة", "callback_data": "RISK"}],
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
    return {"inline_keyboard": [
        [{"text": "⚡ فحص مجدداً", "callback_data": "SCANALL"},
         {"text": "🏠 القائمة الرئيسية", "callback_data": "MAIN"}]
    ]}

# ==============================================
# 📊 دوال جلب البيانات والمؤشرات الفنية
# ==============================================
def fetch(ticker, interval, period, min_bars=50):
    try:
        df = yf.download(
            ticker,
            interval=interval,
            period=period,
            progress=False,
            auto_adjust=True,
            group_by="column"
        )
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
        logging.warning(f"Fetch Error {ticker}: {e}")
        return pd.DataFrame()

def EMA(s, p):
    return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100 / (1 + gain / (loss + 1e-9))

def MACD(s):
    fast = s.ewm(span=12, adjust=False).mean()
    slow = s.ewm(span=26, adjust=False).mean()
    macd_line = fast - slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line

def BB(s, p=20):
    mid = s.rolling(p).mean()
    std = s.rolling(p).std(ddof=0)
    return mid + 2 * std, mid, mid - 2 * std

def ATR(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()

def ADX(h, l, c, p=14):
    up = h.diff()
    down = -l.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(p, min_periods=1).mean().replace(0, 1e-9)
    pdi = 100 * pd.Series(plus_dm, index=h.index).rolling(p).mean() / atr
    ndi = 100 * pd.Series(minus_dm, index=h.index).rolling(p).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p, min_periods=1).mean(), pdi, ndi

def STOCH(h, l, c, k=14, d=3):
    lowest_low = l.rolling(k, min_periods=1).min()
    highest_high = h.rolling(k, min_periods=1).max()
    stoch_k = 100 * (c - lowest_low) / (highest_high - lowest_low + 1e-9)
    return stoch_k, stoch_k.rolling(d, min_periods=1).mean()

def CANDLES(df):
    res = {"buy": [], "sell": []}
    if len(df) < 5:
        return res
    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values

    def body(i): return abs(C[i] - O[i])
    def range_(i): return H[i] - L[i] + 1e-9
    def bull(i): return C[i] > O[i]
    def bear(i): return C[i] < O[i]
    def lower_wick(i): return min(C[i], O[i]) - L[i]
    def upper_wick(i): return H[i] - max(C[i], O[i])

    i = -1
    if bull(i) and body(i) > 0 and lower_wick(i) >= 1.5 * body(i) and upper_wick(i) <= body(i) * 0.5:
        res["buy"].append("Pin Bar ↑")
    if bear(-2) and bull(i) and body(i) > body(-2) * 0.8:
        res["buy"].append("Engulfing ↑")
    if bear(i) and body(i) > 0 and upper_wick(i) >= 1.5 * body(i) and lower_wick(i) <= body(i) * 0.5:
        res["sell"].append("Pin Bar ↓")
    if bull(-2) and bear(i) and body(i) > body(-2) * 0.8:
        res["sell"].append("Engulfing ↓")
    return res

# ==============================================
# 🧮 منطق التحليل
# ==============================================
def analyze(df):
    if df is None or df.empty or len(df) < 30:
        return None, 0, {}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    e8 = float(EMA(close, 8).iloc[-1])
    e21 = float(EMA(close, 21).iloc[-1])
    rsi = float(RSI(close, 14).iloc[-1])
    macd_line, signal_line, _ = MACD(close)
    b_upper, b_mid, b_lower = BB(close)
    atr_val = max(float(ATR(high, low, close, 14).iloc[-1]), 1e-5)
    stoch_k, _ = STOCH(high, low, close)

    buy = 0
    sell = 0
    reasons_buy = []
    reasons_sell = []

    # شروط مبسطة وفعالة
    if e8 > e21:
        buy += 3
        reasons_buy.append("EMA صاعد")
    if e8 < e21:
        sell += 3
        reasons_sell.append("EMA هابط")

    if rsi < 35:
        buy += 3
        reasons_buy.append(f"RSI {rsi:.0f} تشبع بيع")
    if rsi > 65:
        sell += 3
        reasons_sell.append(f"RSI {rsi:.0f} تشبع شراء")

    if macd_line.iloc[-2] < signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
        buy += 3
        reasons_buy.append("MACD تقاطع صاعد")
    if macd_line.iloc[-2] > signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
        sell += 3
        reasons_sell.append("MACD تقاطع هابط")

    current_price = float(close.iloc[-1])
    if current_price <= b_lower.iloc[-1] * 1.01:
        buy += 2
        reasons_buy.append("دعم بولينجر")
    if current_price >= b_upper.iloc[-1] * 0.99:
        sell += 2
        reasons_sell.append("مقاومة بولينجر")

    if stoch_k.iloc[-1] < 25:
        buy += 2
        reasons_buy.append("ستوكاستيك منخفض")
    if stoch_k.iloc[-1] > 75:
        sell += 2
        reasons_sell.append("ستوكاستيك مرتفع")

    patterns = CANDLES(df)
    weights = {"Pin Bar ↑": 2, "Engulfing ↑": 3, "Pin Bar ↓": 2, "Engulfing ↓": 3}
    for p in patterns["buy"]:
        buy += weights.get(p, 1)
        reasons_buy.append(p)
    for p in patterns["sell"]:
        sell += weights.get(p, 1)
        reasons_sell.append(p)

    # فلترات أمان
    if buy > sell and rsi > 90:
        return None, 0, {}
    if sell > buy and rsi < 10:
        return None, 0, {}

    score = max(buy, sell)
    logging.info(f"تحليل: BUY={buy} | SELL={sell} | SCORE={score}")

    if score < MIN_SCORE:
        return None, 0, {}

    total = buy + sell
    confidence = int((score / total) * 100) if total > 0 else 50
    power = min(100, int(score * 3))

    stake = round(BALANCE * (RISK_PCT / 100), 2)
    profit = round(stake * (PAYOUT / 100), 2)

    details = {
        "price": round(current_price, 5),
        "rsi": round(rsi, 1),
        "conf": confidence,
        "power": power,
        "stake": stake,
        "profit": profit,
        "sl_b": round(current_price - 1.5 * atr_val, 5),
        "tp_b": round(current_price + 2.5 * atr_val, 5),
        "sl_s": round(current_price + 1.5 * atr_val, 5),
        "tp_s": round(current_price - 2.5 * atr_val, 5),
        "why": " | ".join(reasons_buy if buy > sell else reasons_sell)
    }

    return ("BUY", score, details) if buy > sell else ("SELL", score, details)

# ==============================================
# 📝 سجل الإشارات والتقارير
# ==============================================
def log_signal(pair, signal, score, power, conf, timeframe, expiry):
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["التاريخ", "الزوج", "الإشارة", "النقاط", "القوة", "الثقة", "الإطار", "المدة"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            pair, signal, score, power, conf, timeframe, expiry
        ])

def get_performance_report():
    if not os.path.isfile(LOG_FILE):
        return "📈 لا يوجد سجل إشارات حتى الآن"
    try:
        df = pd.read_csv(LOG_FILE)
        total = len(df)
        buy_count = len(df[df["الإشارة"] == "BUY"])
        sell_count = len(df[df["الإشارة"] == "SELL"])
        avg_power = round(df["القوة"].mean(), 1)
        avg_conf = round(df["الثقة"].mean(), 1)

        return (
            f"📊 <b>تقرير أداء البوت</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 إجمالي الإشارات: <code>{total}</code>\n"
            f"🟢 إشارات شراء: <code>{buy_count}</code>\n"
            f"🔴 إشارات بيع: <code>{sell_count}</code>\n"
            f"💪 متوسط القوة: <code>{avg_power}/100</code>\n"
            f"🎯 متوسط الثقة: <code>{avg_conf}%</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    except Exception:
        return "📈 سجل الإشارات فارغ حالياً"

# ==============================================
# 📄 تنسيق الرسائل
# ==============================================
def format_signal(pair, tf_name, sig, det, exp):
    arrow = "▲" if sig == "BUY" else "▼"
    color = "🟢" if sig == "BUY" else "🔴"
    sl = det["sl_b"] if sig == "BUY" else det["sl_s"]
    tp = det["tp_b"] if sig == "BUY" else det["tp_s"]

    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>BASILISK SIGNAL v2.2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 الزوج: <code>{otc(pair)}</code>\n"
        f"⏱ الإطار: <code>{tf_name}</code> | المدة: <code>{exp}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow} {color} <b>{sig}</b> {arrow}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💪 القوة: <code>{det['power']}/100</code>\n"
        f"🎯 الثقة: <code>{det['conf']}%</code>\n"
        f"💰 السعر الحالي: <code>{det['price']}</code>\n"
        f"📉 RSI: <code>{det['rsi']}</code>\n"
        f"🛑 إيقاف الخسارة: <code>{sl}</code>\n"
        f"🎯 هدف الربح: <code>{tp}</code>\n"
        f"💵 المخاطرة: <code>${det['stake']}</code> | الربح المتوقع: <code>+${det['profit']}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 السبب: <i>{det['why']}</i>\n"
        f"🕒 الوقت: <code>{datetime.now().strftime('%H:%M:%S')}</code>\n"
        "⚠️ ليس توصية تداول\n"
    )

def format_no_signal(pair, tf_name):
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {otc(pair)} | {tf_name}\n"
        "⚠️ لا توجد إشارة واضحة حالياً\n"
        "جرب إطاراً زمنياً آخر أو انتظر تكوين إشارة جديدة\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

# ==============================================
# 🔍 عملية الفحص
# ==============================================
def do_scan(chat_id=None, pair=None, tfk=None):
    target_chat = chat_id or CHAT_ID

    # فحص زوج محدد
    if pair and tfk:
        cfg = TIMEFRAMES[tfk]
        df = fetch(SYMBOLS[pair], cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)

        if sig:
            msg = format_signal(pair, cfg["label"], sig, det, cfg["exp"])
            send(target_chat, msg, kb_result())
            log_signal(pair, sig, score, det["power"], det["conf"], cfg["label"], cfg["exp"])
        else:
            send(target_chat, format_no_signal(pair, cfg["label"]), kb_result())
        return

    # فحص جميع الأزواج
    found_signals = []
    for name, ticker in SYMBOLS.items():
        time.sleep(FETCH_DELAY)
        cfg = TIMEFRAMES[MAIN_TF]
        df = fetch(ticker, cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)

        if not sig:
            continue

        key = f"{name}|{MAIN_TF}|{sig}"
        if time.time() - last_sig.get(key, 0) < COOLDOWN:
            continue

        last_sig[key] = time.time()
        found_signals.append((name, cfg["label"], sig, score, det, cfg["exp"]))

    if not found_signals:
        send(target_chat, "⚠️ لا توجد إشارات قوية حالياً", kb_main())
        return

    for name, tf, sig, score, det, exp in found_signals:
        send(target_chat, format_signal(name, tf, sig, det, exp), kb_result())
        log_signal(name, sig, score, det["power"], det["conf"], tf, exp)
        time.sleep(1)

# ==============================================
# 🤖 معالجة الأوامر والتفاعل
# ==============================================
def on_command(chat_id, text):
    if text.strip() in ["/start", "/menu"]:
        send(chat_id,
            "⚡ <b>BASILISK v2.2 • النسخة المدمجة النهائية</b>\n\n"
            "✅ يدوي بالكامل — لا يعمل تلقائياً\n"
            "✅ مؤشرات فنية كاملة ومبسطة\n"
            "✅ سجل إشارات وتقرير أداء\n"
            "✅ واجهة تحكم سهلة الاستخدام\n\n"
            "اختر من القائمة أدناه:",
            kb_main()
        )

def on_callback(chat_id, msg_id, query_id, data):
    answer(query_id)

    if data == "MAIN":
        edit(chat_id, msg_id, "القائمة الرئيسية", kb_main())

    elif data == "SCANALL":
        edit(chat_id, msg_id, "🔍 جاري فحص جميع الأزواج... الرجاء الانتظار")
        threading.Thread(target=do_scan, args=(chat_id,), daemon=True).start()

    elif data == "PAIRS":
        edit(chat_id, msg_id, "📊 اختر زوج العملات:", kb_pairs())

    elif data == "REPORT":
        edit(chat_id, msg_id, get_performance_report(), kb_main())

    elif data == "RISK":
        stake = round(BALANCE * (RISK_PCT / 100), 2)
        profit = round(stake * (PAYOUT / 100), 2)
        edit(chat_id, msg_id,
            f"💰 <b>حاسبة المخاطرة</b>\n\n"
            f"💵 الرصيد الحالي: <code>${BALANCE:,.2f}</code>\n"
            f"⚠️ نسبة المخاطرة: <code>{RISK_PCT}%</code>\n"
            f"💸 قيمة الصفقة: <code>${stake}</code>\n"
            f"✅ الربح المحتمل: <code>+${profit}</code>\n"
            f"❌ الخسارة القصوى: <code>-${stake}</code>",
            kb_main()
        )

    elif data.startswith("P:"):
        selected_pair = data[2:]
        edit(chat_id, msg_id, f"⏱ اختر الإطار الزمني لـ {otc(selected_pair)}", kb_tf(selected_pair))

    elif data.startswith("T:"):
        _, selected_pair, selected_tf = data.split(":")
        edit(chat_id, msg_id, f"🔍 جاري تحليل {otc(selected_pair)} على {TIMEFRAMES[selected_tf]['label']}...")
        threading.Thread(target=do_scan, args=(chat_id, selected_pair, selected_tf), daemon=True).start()

# ==============================================
# 🚀 تشغيل البوت والخادم
# ==============================================
def polling_loop():
    last_update_id = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={
                    "offset": last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["message", "callback_query"])
                },
                timeout=35
            ).json()

            for update in res.get("result", []):
                last_update_id = update["update_id"]
                if "message" in update:
                    msg = update["message"]
                    on_command(msg["chat"]["id"], msg.get("text", ""))
                elif "callback_query" in update:
                    cb = update["callback_query"]
                    on_callback(
                        cb["message"]["chat"]["id"],
                        cb["message"]["message_id"],
                        cb["id"],
                        cb["data"]
                    )
        except Exception as e:
            logging.error(f"Polling Error: {e}")
            time.sleep(3)

@app.route("/")
def home():
    return "✅ BASILISK BOT v2.2 • RUNNING"

if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "ضع_رمز_البوت_هنا":
        print("❌ خطأ: الرجاء إدخال BOT_TOKEN الصالح")
    else:
        logging.info("✅ البوت بدأ العمل بنجاح")
        threading.Thread(target=polling_loop, daemon=True).start()
        port = int(os.environ.get("PORT", 8080))
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port)
        except ImportError:
            app.run(host="0.0.0.0", port=port)
