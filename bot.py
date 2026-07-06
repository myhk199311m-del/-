import os, time, requests, threading, logging, csv, json
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ══════════════════════════════════════════════════════════════
# 🔧 الإعدادات — PocketOption OTC Style
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 92 # نسبة الربح في PocketOption OTC

SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X", "AUD/JPY": "AUDJPY=X", "EUR/GBP": "EURGBP=X",
}

TIMEFRAMES = {
    "1د": {"interval":"1m", "period":"1d", "bars":150, "exp_min":1},
    "5د": {"interval":"5m", "period":"5d", "bars":150, "exp_min":5},
}

# ⚙️ إعدادات BASILISK الأصلية
MAIN_TF = "1د" # الفيديو شغال على 1 دقيقة
MIN_SCORE = 7
MIN_ADX = 22 # الفيديو يستخدم ADX قوي
ZIGZAG_DEPTH = 12 # عمق الزجزاج زي الفيديو
COOLDOWN = 60 # دقيقة واحدة بس زي البوت الأصلي
FETCH_DELAY = 1.5

SIGNAL_FILE = "basilisk_signals.json"
LOG_FILE = "basilisk_log.csv"
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

# ══ إدارة الكولداون ═══════════════════════════════════════════
def load_last_sig():
    try:
        if os.path.exists(SIGNAL_FILE):
            with open(SIGNAL_FILE, 'r') as f: return json.load(f)
    except: pass
    return {}

def save_last_sig(data):
    try:
        with open(SIGNAL_FILE, 'w') as f: json.dump(data, f)
    except: pass

last_sig = load_last_sig()
lock = threading.Lock()

# ══ Flask و Telegram ══════════════════════════════════════════
app = Flask(__name__)
@app.route("/")
def home(): return "✅ BASILISK OTC v4.0 • ONLINE"

def tg(method, data):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=15)
        return r.json()
    except: return {"ok":False}

def send(cid, txt, kb=None):
    p = {"chat_id":cid,"text":txt,"parse_mode":"HTML","disable_web_page_preview":True}
    if kb: p["reply_markup"] = kb
    tg("sendMessage",p)

def edit(cid, mid, txt, kb=None):
    p = {"chat_id":cid,"message_id":mid,"text":txt,"parse_mode":"HTML"}
    if kb: p["reply_markup"] = kb
    tg("editMessageText",p)

def answer(cbid): tg("answerCallbackQuery",{"callback_query_id":cbid})

# ══ واجهة الأزرار - نفس الفيديو ═════════════════════════════════
def kb_main():
    return {"inline_keyboard":[
        [{"text":"⚡ SCAN","callback_data":"SCAN"}],
        [{"text":"RESET","callback_data":"RESET"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows = [[{"text":f"{p} OTC","callback_data":f"P:{p}"} for p in names[i:i+2]] for i in range(0, len(names), 2)]
    rows.append([{"text":"◄ BACK","callback_data":"MAIN"}])
    return {"inline_keyboard":rows}

# ══ دوال مساعدة ═══════════════════════════════════════════════
def fetch(ticker, interval, period, min_bars=150):
    for attempt in range(3):
        try:
            if attempt > 0: time.sleep(10*attempt)
            df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            df = df[["open","high","low","close"]].dropna()
            if len(df) < min_bars: continue
            return df.copy()
        except: pass
    return pd.DataFrame()

# ══ المؤشرات + ZigZag ═════════════════════════════════════════
def EMA(s,p): return s.ewm(span=p, adjust=False).mean()
def RSI(s,p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/(l + 1e-9))

def ADX(h,l,c,p=14):
    up = h.diff(); down = -l.diff()
    pdm = np.where((up>down) & (up>0), up, 0.0)
    ndm = np.where((down>up) & (down>0), down, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.rolling(p).mean().replace(0, 1e-9)
    pdi = 100 * pd.Series(pdm, index=h.index).rolling(p).mean() / atr_
    ndi = 100 * pd.Series(ndm, index=h.index).rolling(p).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p).mean()

def ZIGZAG(df, depth=12):
    """نفس خط الزجزاج الأصفر اللي بالفيديو"""
    hi, lo = df['high'], df['low']
    peaks = []; troughs = []
    for i in range(depth, len(df)-depth):
        if hi.iloc[i] == hi.iloc[i-depth:i+depth+1].max():
            peaks.append((i, hi.iloc[i]))
        if lo.iloc[i] == lo.iloc[i-depth:i+depth+1].min():
            troughs.append((i, lo.iloc[i]))

    # دمج وترتيب
    points = sorted(peaks + troughs, key=lambda x: x[0])
    if len(points) < 4: return None, None

    # كشف النموذج: M-Top أو W-Bottom
    pattern = "NONE"
    if len(points) >= 5:
        p = [x[1] for x in points[-5:]]
        # M-Top: قمة - قاع - قمة أقل - قاع - كسر
        if p[0] > p[2] and p[1] < p[3] and p[4] < p[3]:
            pattern = "M-TOP"
        # W-Bottom: قاع - قمة - قاع أعلى - قمة - كسر
        if p[0] < p[2] and p[1] > p[3] and p[4] > p[3]:
            pattern = "W-BOTTOM"

    return points, pattern

# ══ التحليل — BASILISK Logic ═══════════════════════════════════
def analyze_basilisk(df):
    if df is None or len(df) < 50: return "WAIT", 0, {}
    cl, hi, lo = df["close"], df["high"], df["low"]

    e9, e21 = EMA(cl,9), EMA(cl,21)
    rv = float(RSI(cl,7).iloc[-1]) # RSI سريع زي الفيديو
    adx_val = float(ADX(hi,lo,cl,14).iloc[-1])
    points, pattern = ZIGZAG(df, ZIGZAG_DEPTH)

    buy = sell = 0; rb = []; rs = []

    # 1. فلتر ADX قوي - البوت ما يدخل إلا بترند
    if adx_val < MIN_ADX:
        return "WAIT", 0, {"reason": f"ADX ضعيف {adx_val:.0f}", "adx": adx_val}

    # 2. فلتر الزجزاج والنماذج - أهم شيء في البوت الأصلي
    if pattern == "M-TOP":
        sell += 5; rs.append("نموذج M-TOP")
    elif pattern == "W-BOTTOM":
        buy += 5; rb.append("نموذج W-BOTTOM")

    # 3. EMA Cross
    if e9.iloc[-2] < e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
        buy += 3; rb.append("تقاطع EMA صاعد")
    if e9.iloc[-2] > e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
        sell += 3; rs.append("تقاطع EMA هابط")

    # 4. RSI
    if rv < 30: buy += 2; rb.append(f"RSI {rv:.0f}")
    if rv > 70: sell += 2; rs.append(f"RSI {rv:.0f}")

    # 5. آخر شمعة - Pin Bar
    last = df.iloc[-2] # الشمعة المغلقة
    body = abs(last['close'] - last['open'])
    rng = last['high'] - last['low'] + 1e-9
    if body/rng < 0.3: # دوجي أو بن بار
        if last['close'] > last['open']: buy += 2; rb.append("Pin Bar ↑")
        else: sell += 2; rs.append("Pin Bar ↓")

    # فلتر الحماية
    if buy > sell and rv > 85: return "WAIT", 0, {"reason": "تشبع شراء"}
    if sell > buy and rv < 15: return "WAIT", 0, {"reason": "تشبع بيع"}

    score = max(buy, sell)
    if score < MIN_SCORE or abs(buy-sell) < 2:
        return "WAIT", 0, {"reason": "إشارات متضاربة", "adx": adx_val}

    conf = min(99, int((score/15)*100))
    stake = round(BALANCE * (RISK_PCT/100), 2)
    profit = round(stake * (PAYOUT/100), 2)

    det = {
        "price": round(float(cl.iloc[-1]),5),
        "rsi": round(rv,1), "adx": round(adx_val,1),
        "pattern": pattern, "conf": conf, "power": score,
        "stake": stake, "profit": profit, "payout": PAYOUT,
        "exp": TIMEFRAMES[MAIN_TF]["exp_min"]
    }

    if buy > sell:
        det["why"] = " | ".join(rb)
        return "BUY", score, det
    else:
        det["why"] = " | ".join(rs)
        return "SELL", score, det

# ══ تنسيق الرسالة - نفس شكل الفيديو ═══════════════════════════════
def fmt_basilisk(pair, sig, det):
    if sig == "WAIT":
        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>BASILISK</b>\n"
            f"{pair} OTC | {TIMEFRAMES[MAIN_TF]['exp_min']}m\n\n"
            f"<b>WAIT</b>\n"
            f"{det.get('reason','')}\n"
            f"ADX: {det.get('adx',0):.0f}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    color = "🟢" if sig=="BUY" else "🔴"
    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>BASILISK</b>\n"
        f"{pair} | {PAYOUT}% | {det['exp']}m\n\n"
        f"<b>{sig}</b>\n\n"
        f"<b>RISK CALCULATOR</b>\n"
        f"${det['stake']:.2f}\n\n"
        f"<b>Balance</b>\n"
        f"${BALANCE:.2f}\n"
        f"<b>Potential profit</b>\n"
        f"+${det['profit']:.2f}\n"
        f"<b>Potential loss</b>\n"
        f"-${det['stake']:.2f}\n"
        f"<b>Win rate</b>\n"
        f"{det['conf']}.0%\n\n"
        f"<b>Pattern:</b> {det['pattern']}\n"
        f"<b>ADX:</b> {det['adx']:.0f} | <b>RSI:</b> {det['rsi']:.0f}\n"
        f"<b>Reason:</b> {det['why']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Not financial advice"
    )

# ══ الفحص ════════════════════════════════════════════════
def do_scan(chat_id, pair=None):
    pairs = [pair] if pair else list(SYMBOLS.keys())

    for name in pairs:
        time.sleep(FETCH_DELAY)
        df = fetch(SYMBOLS[name], TIMEFRAMES[MAIN_TF]["interval"], TIMEFRAMES[MAIN_TF]["period"])
        sig, score, det = analyze_basilisk(df)

        key = f"{name}|{MAIN_TF}"
        with lock:
            if time.time() - last_sig.get(key, 0) < COOLDOWN and sig!= "WAIT": continue
            last_sig[key] = time.time()
            save_last_sig(last_sig)

        send(chat_id, fmt_basilisk(name, sig, det), kb_main())
        if pair: break # لو زوج محدد نرسل واحدة فقط

# ══ معالجة الأوامر ═══════════════════════════════════════════
def on_cmd(cid, txt):
    if txt in ["/start", "/menu"]:
        send(cid, "⚡ <b>BASILISK OTC v4.0</b>\nاضغط SCAN للبدء", kb_main())

def on_cb(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "⚡ <b>BASILISK OTC v4.0</b>", kb_main())
    elif data == "SCAN":
        edit(cid, mid, "🔍 Scanning market...")
        threading.Thread(target=do_scan, args=(cid,)).start()
    elif data == "RESET":
        edit(cid, mid, "✅ Reset Done", kb_main())
    elif data.startswith("P:"):
        pair = data[2:]
        edit(cid, mid, f"🔍 Scanning {pair}...")
        threading.Thread(target=do_scan, args=(cid, pair)).start()

# ══ التشغيل ══════════════════════════════════════════════════
def polling():
    last = 0
    while True:
        try:
            res = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last+1}&timeout=30").json()
            for upd in res.get("result", []):
                last = upd["update_id"]
                if "message" in upd:
                    msg = upd["message"]
                    on_cmd(msg["chat"]["id"], msg.get("text", ""))
                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    on_cb(cb["message"]["chat"]["id"], cb["message"]["message_id"], cb["id"], cb["data"])
        except Exception as e:
            logging.error(f"polling: {e}")
            time.sleep(3)

if __name__ == "__main__":
    if BOT_TOKEN == "ضع_رمز_البوت_هنا":
        print("❌ ضيف BOT_TOKEN")
    else:
        threading.Thread(target=polling, daemon=True).start()
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
