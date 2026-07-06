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
PAYOUT = 85

SYMBOLS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X", "AUD/JPY": "AUDJPY=X", "EUR/GBP": "EURGBP=X",
}

TIMEFRAMES = {
    "1د": {"interval":"1m", "period":"1d", "bars":150, "exp_min":1},
    "5د": {"interval":"5m", "period":"5d", "bars":150, "exp_min":5},
}

# ⚙️ إعدادات BASILISK - بعد التعديل
MAIN_TF = "1د"
MIN_SCORE = 4 # كان 7 - خففناه
MIN_ADX = 15 # كان 22 - خففناه
ZIGZAG_DEPTH = 8 # كان 12 - صار يلقط نماذج أكثر
FETCH_DELAY = 1.5

SIGNAL_FILE = "basilisk_signals.json"
LOG_FILE = "basilisk_log.csv"
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

app = Flask(__name__)
@app.route("/")
def home(): return "✅ BASILISK OTC v5.1 • MANUAL MODE"

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

# ══ واجهة الأزرار - يدوي فقط ═════════════════════════════════
def kb_main():
    return {"inline_keyboard":[
        [{"text":"📊 PAIRS","callback_data":"PAIRS"}],
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

# ══ المؤشرات ═════════════════════════════════════════
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

def ATR(h,l,c,p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def BB(s,p=20,std=2):
    ma = s.rolling(p).mean()
    stdv = s.rolling(p).std()
    upper = ma + stdv*std
    lower = ma - stdv*std
    return upper, ma, lower

def ZIGZAG(df, depth=8): # قللنا العمق
    hi, lo = df['high'], df['low']
    peaks = []; troughs = []
    for i in range(depth, len(df)-depth):
        if hi.iloc[i] == hi.iloc[i-depth:i+depth+1].max():
            peaks.append((i, hi.iloc[i]))
        if lo.iloc[i] == lo.iloc[i-depth:i+depth+1].min():
            troughs.append((i, lo.iloc[i]))

    points = sorted(peaks + troughs, key=lambda x: x[0])
    if len(points) < 4: return None, "NONE"

    pattern = "NONE"
    if len(points) >= 5:
        p = [x[1] for x in points[-5:]]
        if p[0] > p[2] and p[1] < p[3] and p[4] < p[3]:
            pattern = "M-TOP"
        if p[0] < p[2] and p[1] > p[3] and p[4] > p[3]:
            pattern = "W-BOTTOM"

    return points, pattern

def detect_candle_pattern(df):
    o, h, l, c = df['open'].iloc[-2], df['high'].iloc[-2], df['low'].iloc[-2], df['close'].iloc[-2]
    o2, c2 = df['open'].iloc[-3], df['close'].iloc[-3]
    body = abs(c - o)
    rng = h - l + 1e-9

    # Engulfing
    if c > o and o < c2 and c > o2 and c2 < o2: return "Engulfing ↑"
    if c < o and o > c2 and c < o2 and c2 > o2: return "Engulfing ↓"

    # Evening/Morning Star
    if len(df) >= 4:
        o3, c3 = df['open'].iloc[-4], df['close'].iloc[-4]
        if c3 > o3 and abs(c2-o2)/rng < 0.3 and c < o and c < (o3+c3)/2:
            return "Evening Star ↓"
        if c3 < o3 and abs(c2-o2)/rng < 0.3 and c > o and c > (o3+c3)/2:
            return "Morning Star ↑"

    return "Pin Bar"

# ══ التحليل — BASILISK Logic بعد التعديل ═══════════════════════════════════
def analyze_basilisk(df):
    if df is None or len(df) < 50:
        return "NO SIGNAL", 0, {"reason": "بيانات غير كافية"}

    cl, hi, lo = df["close"], df["high"], df["low"]
    e9, e21 = EMA(cl,9), EMA(cl,21)
    rv = float(RSI(cl,7).iloc[-1])
    adx_val = float(ADX(hi,lo,cl,14).iloc[-1])
    atr_val = float(ATR(hi,lo,cl,14).iloc[-1])
    bb_u, bb_m, bb_l = BB(cl,20,2)
    points, pattern = ZIGZAG(df, ZIGZAG_DEPTH)
    candle_pat = detect_candle_pattern(df)

    buy = sell = 0; rb = []; rs = []

    # تعديل 1: خففنا شرط ADX من 22 إلى 15
    if adx_val < MIN_ADX:
        return "WAIT", 0, {"reason": "السوق ضعيف", "adx": adx_val, "rsi": rv, "atr": atr_val}

    # نقاط ZIGZAG
    if pattern == "M-TOP": sell += 4; rs.append("M-TOP") # كانت 5
    elif pattern == "W-BOTTOM": buy += 4; rb.append("W-BOTTOM") # كانت 5

    # تقاطع EMA
    if e9.iloc[-2] < e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
        buy += 3; rb.append("EMA Bullish")
    if e9.iloc[-2] > e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
        sell += 3; rs.append("EMA Bearish")

    # RSI - خففنا الحدود
    if rv < 35: buy += 2; rb.append(f"RSI {rv:.0f}") # كان 30
    if rv > 65: sell += 2; rs.append(f"RSI {rv:.0f}") # كان 70

    # نقاط إضافية للشموع
    if "↑" in candle_pat: buy += 2; rb.append(candle_pat)
    if "↓" in candle_pat: sell += 2; rs.append(candle_pat)

    # تعديل 2: حذفنا شرط "إشارات متضاربة" الغبي
    # الكود القديم كان يلغي الصفقة لو RSI > 85 مع شراء، وهذا يخرب أقوى الصفقات

    score = max(buy, sell)
    # تعديل 3: خففنا MIN_SCORE من 7 إلى 4 وحذفنا شرط الفرق abs(buy-sell) < 2
    if score < MIN_SCORE:
        return "WAIT", 0, {"reason": "إشارة ضعيفة", "adx": adx_val, "rsi": rv, "atr": atr_val}

    conf = min(99, int((score/12)*100)) # عدلنا القسمة عشان النسبة تصير منطقية
    stake = round(BALANCE * (RISK_PCT/100), 2)
    profit = round(stake * (PAYOUT/100), 2)
    be = round(100/(1+PAYOUT/100), 1)

    strength = "WEAK+" if conf < 70 else "STRONG"
    stars = "⭐⭐" if conf < 70 else "⭐⭐⭐"

    det = {
        "price": round(float(cl.iloc[-1]),5),
        "rsi": round(rv,1), "adx": round(adx_val,1), "atr": round(atr_val,5),
        "bb_u": round(bb_u.iloc[-1],5), "bb_l": round(bb_l.iloc[-1],5),
        "pattern": pattern, "candle": candle_pat, "conf": conf, "power": score,
        "stake": stake, "profit": profit, "payout": PAYOUT, "be": be,
        "strength": strength, "stars": stars,
        "exp": TIMEFRAMES[MAIN_TF]["exp_min"]
    }

    if buy > sell:
        det["why"] = " | ".join(rb)
        return "BUY", score, det
    else:
        det["why"] = " | ".join(rs)
        return "SELL", score, det

# ══ تنسيق الرسالة ═══════════════════════════════
def fmt_basilisk(pair, sig, det):
    if sig == "NO SIGNAL":
        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>NO SIGNAL</b>\n"
            f"بيانات غير كافية\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

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

    arrow = "▲" if sig=="BUY" else "▼"
    circle = "🟢" if sig=="BUY" else "🔴"
    progress = "⬛⬛⬛⬛⬜⬜⬜⬜" if det['conf'] < 70 else "⬛⬛⬛⬛⬜⬜⬜⬜"

    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK SIGNAL • LIVE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 PAIR: {pair} OTC\n"
        f"💰 PAYOUT: {det['payout']}%\n"
        f"⏱ TIME: 0{TIMEFRAMES[MAIN_TF]['exp_min']}:00\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow} {circle} <b>{sig}</b> {arrow}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {det['power']} / 12 {progress}\n"
        f"🏆 {det['strength']} {det['stars']}\n"
        f"🎯 Confidence: {det['conf']}%\n"
        f"🕯️ Pattern: {det['candle']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>RISK CALCULATOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: ${BALANCE:,.2f}\n"
        f"⚠️ Risk {RISK_PCT}%: ${det['stake']:.1f}\n"
        f"✅ Potential Profit: +${det['profit']:.1f}\n"
        f"❌ Potential Loss: -${det['stake']:.1f}\n"
        f"📈 Break-even: ≥ {det['be']}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 RSI:{det['rsi']} ADX:{det['adx']} ATR:{det['atr']:.5f}\n"
        f"📊 BB: {det['bb_u']:.5f} | {det['bb_l']:.5f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"☑️ {det['why']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Not financial advice"
    )

# ══ الفحص اليدوي ════════════════════════════════════════════════
def do_scan(chat_id, pair):
    edit(chat_id, None, f"🔍 Scanning {pair} OTC | {MAIN_TF}...")
    time.sleep(0.5)

    df = fetch(SYMBOLS[pair], TIMEFRAMES[MAIN_TF]["interval"], TIMEFRAMES[MAIN_TF]["period"])
    sig, score, det = analyze_basilisk(df)

    send(chat_id, fmt_basilisk(pair, sig, det), kb_main())

# ══ معالجة الأوامر ═══════════════════════════════════════════
def on_cmd(cid, txt):
    if txt in ["/start", "/menu"]:
        send(cid, "⚡ <b>BASILISK OTC v5.1</b>\n\nاختر زوج للتحليل:", kb_main())

def on_cb(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "⚡ <b>BASILISK OTC v5.1</b>\n\nاختر زوج للتحليل:", kb_main())
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر الزوج:", kb_pairs())
    elif data == "RESET":
        edit(cid, mid, "✅ Reset Done", kb_main())
    elif data.startswith("P:"):
        pair = data[2:]
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
