import os, time, requests, threading, logging, csv
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ══════════════════════════════════════════════════════════════
# 🔧 الإعدادات — سهلة التعديل
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID   = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE   = float(os.environ.get("BALANCE", "1000"))
RISK_PCT  = float(os.environ.get("RISK_PCT", "2"))

SYMBOLS = {
    "AUD/USD": "AUDUSD=X", "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X", "AUD/CAD": "AUDCAD=X",
    "AUD/CHF": "AUDCHF=X", "CAD/CHF": "CADCHF=X",
    "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "GBP/AUD": "GBPAUD=X", "XAU/USD": "GC=F",
}

TIMEFRAMES = {
    "1د":  {"interval":"1m",  "period":"1d",  "label":"1 دقيقة",  "bars":60,  "exp":"01:00"},
    "5د":  {"interval":"5m",  "period":"5d",  "label":"5 دقائق",  "bars":60,  "exp":"05:00"},
    "15د": {"interval":"15m", "period":"10d", "label":"15 دقيقة", "bars":60,  "exp":"15:00"},
    "30د": {"interval":"30m", "period":"15d", "label":"30 دقيقة", "bars":60,  "exp":"30:00"},
    "1س":  {"interval":"60m", "period":"30d", "label":"ساعة",     "bars":60,  "exp":"01:00:00"},
}

# ⚙️ إعدادات جديدة مُحسّنة
MAIN_TF      = "5د"
CONFIRM_TFS  = []       # ألغينا التأكيد من إطار آخر لزيادة الإشارات
MIN_CONFIRM  = 0
COOLDOWN     = 300      # تقليل مدة الانتظار بين الإشارات
MIN_SCORE    = 5        # خفض الحد الأدنى للنقاط لظهور الإشارات
FETCH_DELAY  = 2.0

LOG_FILE = "signals_log.csv"
last_sig = {}
lock     = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ══ Flask و Telegram ══════════════════════════════════════════
app = Flask(__name__)
@app.route("/")
def home(): return "✅ BASILISK BOT v2.1 • MANUAL MODE"

def tg(method, data):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=15)
        return r.json()
    except: return {"ok":False}

def send(cid, txt, kb=None):
    p = {"chat_id":cid,"text":txt,"parse_mode":"HTML","disable_web_page_preview":True}
    if kb: p["reply_markup"] = kb
    for _ in range(4):
        if tg("sendMessage",p).get("ok"): return True
        p["parse_mode"] = ""
        time.sleep(1.5)
    return False

def edit(cid, mid, txt, kb=None):
    p = {"chat_id":cid,"message_id":mid,"text":txt,"parse_mode":"HTML"}
    if kb: p["reply_markup"] = kb
    tg("editMessageText",p)

def answer(cbid): tg("answerCallbackQuery",{"callback_query_id":cbid})

# ══ واجهة الأزرار ════════════════════════════════════════════
def otc(name): return f"{name} OTC"

def kb_main():
    return {"inline_keyboard":[
        [{"text":"⚡ فحص الكل","callback_data":"SCANALL"}],
        [{"text":"📊 اختر زوج","callback_data":"PAIRS"},
         {"text":"🏆 أقوى إشارة","callback_data":"BEST"}],
        [{"text":"📈 تقرير الأداء","callback_data":"REPORT"},
         {"text":"💰 حاسبة المخاطرة","callback_data":"RISK"}],
        [{"text":"⚙️ الإعدادات","callback_data":"SETTINGS"},
         {"text":"❓ مساعدة","callback_data":"HELP"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows = []
    for i in range(0, len(names), 2):
        rows.append([{"text":f"● {p} OTC","callback_data":f"P:{p}"} for p in names[i:i+2]])
    rows.append([{"text":"◄ رجوع","callback_data":"MAIN"}])
    return {"inline_keyboard":rows}

def kb_tf(pair):
    tfs = list(TIMEFRAMES.items())
    rows = []
    for i in range(0, len(tfs), 3):
        rows.append([{"text":v["exp"],"callback_data":f"T:{pair}:{k}"} for k,v in tfs[i:i+3]])
    rows.append([{"text":"◄ رجوع","callback_data":"PAIRS"}])
    return {"inline_keyboard":rows}

def kb_result():
    return {"inline_keyboard":[
        [{"text":"⚡ فحص مجدداً","callback_data":"SCANALL"},
         {"text":"🏆 أقوى إشارة","callback_data":"BEST"}],
        [{"text":"📊 زوج آخر","callback_data":"PAIRS"},
         {"text":"🏠 القائمة","callback_data":"MAIN"}],
    ]}

# ══ دوال مساعدة ═══════════════════════════════════════════════
def calc_risk(score, payout=85):
    stake = round(BALANCE * (RISK_PCT/100), 2)
    profit = round(stake * (payout/100), 2)
    loss = stake
    bew = round(100/(1+(payout/100)), 1)
    return stake, profit, loss, bew

def fetch(ticker, interval, period, min_bars=60):
    for attempt in range(4):
        try:
            if attempt > 0: time.sleep(20*attempt)
            df = yf.download(ticker, interval=interval, period=period,
                             progress=False, auto_adjust=True, group_by="column")
            if df is None or df.empty: continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            if not {"open","high","low","close"}.issubset(set(df.columns)): continue
            df = df[["open","high","low","close"]].dropna()
            df = df[df["high"] != df["low"]]
            if len(df) < min_bars: continue
            return df.copy()
        except Exception as e:
            err = str(e)
            if "Too Many Requests" in err or "RateLimit" in err:
                time.sleep(30*(attempt+1))
            else:
                logging.warning(f"fetch {ticker}: {err[:60]}")
    return pd.DataFrame()

# ══ المؤشرات ═════════════════════════════════════════════════
def EMA(s,p): return s.ewm(span=p, adjust=False).mean()
def RSI(s,p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/(l + 1e-9))
def MACD(s):
    f = s.ewm(span=12, adjust=False).mean()
    sl = s.ewm(span=26, adjust=False).mean()
    ln = f - sl; sg = ln.ewm(span=9, adjust=False).mean()
    return ln, sg, ln - sg
def BB(s,p=20):
    m = s.rolling(p).mean(); std = s.rolling(p).std(ddof=0)
    return m + 2*std, m, m - 2*std
def ATR(h,l,c,p=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()
def ADX(h,l,c,p=14):
    up = h.diff(); down = -l.diff()
    pdm = np.where((up>down) & (up>0), up, 0.0)
    ndm = np.where((down>up) & (down>0), down, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.rolling(p, min_periods=1).mean()
    atr_ = atr_.replace(0, 1e-9)  # تجنب القسمة على صفر
    pdi = 100 * pd.Series(pdm, index=h.index).rolling(p).mean() / atr_
    ndi = 100 * pd.Series(ndm, index=h.index).rolling(p).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p, min_periods=1).mean(), pdi, ndi
def STOCH(h,l,c,k=14,d=3):
    lk = l.rolling(k, min_periods=1).min()
    hk = h.rolling(k, min_periods=1).max()
    sk = 100 * (c - lk) / (hk - lk + 1e-9)
    return sk, sk.rolling(d, min_periods=1).mean()
def PIVOT(df):
    h = float(df["high"].tail(20).max())
    l = float(df["low"].tail(20).min())
    c = float(df["close"].iloc[-1])
    pv = (h + l + c) / 3
    return round(pv - (h-l),5), round(2*pv - h,5), round(pv,5), round(2*pv - l,5), round(pv + (h-l),5)

def CANDLES(df):
    res = {"buy":[], "sell":[]}
    if len(df) < 5: return res
    O = df["open"].values; H = df["high"].values
    L = df["low"].values; C = df["close"].values
    def body(i): return abs(C[i] - O[i])
    def rng(i): return H[i] - L[i] + 1e-9
    def bull(i): return C[i] > O[i]
    def bear(i): return C[i] < O[i]
    def uw(i): return H[i] - max(C[i], O[i])
    def lw(i): return min(C[i], O[i]) - L[i]
    i = -1
    if bull(i) and body(i)>0 and lw(i)>=1.5*body(i) and uw(i)<=body(i)*0.5:
        res["buy"].append("Pin Bar ↑")
    if bear(-2) and bull(i) and body(i)>body(-2)*0.8:
        res["buy"].append("Engulfing ↑")
    if bear(i) and body(i)>0 and uw(i)>=1.5*body(i) and lw(i)<=body(i)*0.5:
        res["sell"].append("Pin Bar ↓")
    if bull(-2) and bear(i) and body(i)>body(-2)*0.8:
        res["sell"].append("Engulfing ↓")
    return res

# ══ التحليل — مُرن ومحسّن ════════════════════════════════════
def analyze(df):
    if df is None or df.empty or len(df) < 30: return None, 0, {}
    cl = df["close"].astype(float)
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)

    e8 = float(EMA(cl,8).iloc[-1])
    e21 = float(EMA(cl,21).iloc[-1])
    e50 = float(EMA(cl,50).iloc[-1]) if len(cl)>=50 else e21
    rv = float(RSI(cl).iloc[-1])
    ml,ms,_ = MACD(cl)
    bu,bm,bl = BB(cl)
    av = max(float(ATR(hi,lo,cl).iloc[-1]), 1e-5)
    sk,_ = STOCH(hi,lo,cl)
    adxs,pdis,ndis = ADX(hi,lo,cl)
    s2,s1,pvt,r1,r2 = PIVOT(df)
    p = float(cl.iloc[-1])

    buy = sell = 0; rb = []; rs = []

    # شروط مبسطة
    if e8 > e21: buy +=3; rb.append("EMA صاعد")
    if e8 < e21: sell +=3; rs.append("EMA هابط")
    if rv < 35: buy +=3; rb.append(f"RSI {rv:.0f} تشبع بيع")
    if rv > 65: sell +=3; rs.append(f"RSI {rv:.0f} تشبع شراء")
    if ml.iloc[-2] < ms.iloc[-2] and ml.iloc[-1] > ms.iloc[-1]:
        buy +=3; rb.append("MACD تقاطع صاعد")
    if ml.iloc[-2] > ms.iloc[-2] and ml.iloc[-1] < ms.iloc[-1]:
        sell +=3; rs.append("MACD تقاطع هابط")
    if p <= bl.iloc[-1] * 1.01: buy +=2; rb.append("دعم بولينجر")
    if p >= bu.iloc[-1] * 0.99: sell +=2; rs.append("مقاومة بولينجر")
    if sk.iloc[-1] < 25: buy +=2; rb.append("ستوكاستيك منخفض")
    if sk.iloc[-1] > 75: sell +=2; rs.append("ستوكاستيك مرتفع")

    cds = CANDLES(df)
    w = {"Pin Bar ↑":2,"Engulfing ↑":3,"Pin Bar ↓":2,"Engulfing ↓":3}
    for nm in cds["buy"]: buy += w.get(nm,1); rb.append(nm)
    for nm in cds["sell"]: sell += w.get(nm,1); rs.append(nm)

    # فلتر بسيط فقط
    if buy > sell and rv > 90: return None,0,{}
    if sell > buy and rv < 10: return None,0,{}

    score = max(buy,sell)
    logging.info(f"تحليل: BUY={buy} | SELL={sell} | SCORE={score}")

    if score < MIN_SCORE: return None,0,{}
    conf = int((score/(buy+sell))*100) if (buy+sell) else 50
    power = min(100, int(score*3))

    det = {
        "price":round(p,5),"rsi":round(rv,1),"adx":round(float(adxs.iloc[-1]),1),
        "atr":round(av,5),"bbl":round(float(bl.iloc[-1]),5),"bbu":round(float(bu.iloc[-1]),5),
        "s1":s1,"r1":r1,"pvt":pvt,"sl_b":round(p-1.5*av,5),"tp_b":round(p+2.5*av,5),
        "sl_s":round(p+1.5*av,5),"tp_s":round(p-2.5*av,5),"rr":"1:1.67",
        "stars":"⭐⭐⭐⭐⭐" if power>=75 else "⭐⭐⭐⭐" if power>=60 else "⭐⭐⭐",
        "grade":"STRONG" if power>=75 else "MEDIUM" if power>=60 else "NORMAL",
        "score":score,"conf":conf,"power":power,"bar":"██████████" if power>=75 else "███████░░░"
    }

    if buy > sell:
        det["cds"] = " | ".join(cds["buy"]) if cds["buy"] else "—"
        det["why"] = " | ".join(rb)
        return "BUY", score, det
    else:
        det["cds"] = " | ".join(cds["sell"]) if cds["sell"] else "—"
        det["why"] = " | ".join(rs)
        return "SELL", score, det

def get_multi_timeframe_signal(pair):
    if not CONFIRM_TFS:
        ticker = SYMBOLS[pair]
        cfg = TIMEFRAMES[MAIN_TF]
        df = fetch(ticker, cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)
        return sig, score, det, 0
    # إذا أردت إعادة التأكيد مستقبلاً
    ticker = SYMBOLS[pair]
    main_cfg = TIMEFRAMES[MAIN_TF]
    df_main = fetch(ticker, main_cfg["interval"], main_cfg["period"], main_cfg["bars"])
    sig_main, score_main, det_main = analyze(df_main)
    if not sig_main or score_main < MIN_SCORE: return None,0,{},0
    return sig_main, score_main, det_main, 1

# ══ السجل والتقرير ═══════════════════════════════════════════
def log_signal(pair, sig, score, power, conf, tf, exp):
    exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["التاريخ","الزوج","الإشارة","النقاط","القوة","الثقة","الإطار","المدة"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"), pair, sig, score, power, conf, tf, exp])

def get_report():
    if not os.path.isfile(LOG_FILE):
        return "📈 لا يوجد سجل إشارات حتى الآن"
    try:
        df = pd.read_csv(LOG_FILE)
        total = len(df)
        buy = len(df[df["الإشارة"]=="BUY"])
        sell = len(df[df["الإشارة"]=="SELL"])
        avg_power = round(df["القوة"].mean(),1)
        return (
            f"📊 <b>تقرير الأداء</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"إجمالي الإشارات: {total}\n"
            f"شراء: {buy} | بيع: {sell}\n"
            f"متوسط القوة: {avg_power}/100"
        )
    except:
        return "📈 سجل الإشارات فارغ حالياً"

# ══ تنسيق الرسائل ════════════════════════════════════════════
def fmt(name, tf, sig, det, exp):
    stake, profit, loss, bew = calc_risk(det["score"])
    confirm = det.get("confirmations", "")
    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ BASILISK • {otc(name)}\n"
        f"⏱ {tf} | {exp}\n{confirm}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 BUY ▲' if sig=='BUY' else '🔴 SELL ▼'}\n"
        f"القوة: {det['power']}/100 {det['stars']}\n"
        f"الثقة: {det['conf']}%\n"
        f"السعر: {det['price']}\n"
        f"📉 RSI: {det['rsi']} | ATR: {det['atr']}\n"
        f"📈 SL: {det['sl_b'] if sig=='BUY' else det['sl_s']} | TP: {det['tp_b'] if sig=='BUY' else det['tp_s']}\n"
        f"💰 المخاطرة: {RISK_PCT}% = ${stake}\n"
        f"📋 السبب: {det['why']}\n"
        f"🕒 {datetime.now().strftime('%H:%M:%S')}\n"
        f"⚠️ ليس توصية تداول"
    )

def fmt_no_signal(name, tf):
    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {otc(name)} | {tf}\n"
        f"⚠️ لا توجد إشارة واضحة حالياً\n"
        f"جرب إطاراً زمنياً آخر أو انتظر"
    )

# ══ الفحص اليدوي فقط ════════════════════════════════════════
def do_scan(chat_id=None, pair=None, tfk=None):
    tgt = chat_id or CHAT_ID
    if pair and tfk:
        cfg = TIMEFRAMES[tfk]
        df = fetch(SYMBOLS[pair], cfg["interval"], cfg["period"], cfg["bars"])
        sig, score, det = analyze(df)
        if sig:
            send(tgt, fmt(pair, cfg["label"], sig, det, cfg["exp"]), kb_result())
            log_signal(pair, sig, score, det["power"], det["conf"], cfg["label"], cfg["exp"])
        else:
            send(tgt, fmt_no_signal(pair, cfg["label"]), kb_result())
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
        send(tgt, "⚠️ لا توجد إشارات قوية حالياً", kb_main())
        return

    for name, tf, sig, score, det, exp in found:
        send(tgt, fmt(name, tf, sig, det, exp), kb_result())
        log_signal(name, sig, score, det["power"], det["conf"], tf, exp)
        time.sleep(1)

# ══ معالجة الأوامر ═══════════════════════════════════════════
def on_cmd(cid, txt):
    if txt.strip() in ["/start", "/menu"]:
        send(cid,
            "⚡ <b>BASILISK v2.1</b>\n"
            "✅ يدوي بالكامل — لا فحص تلقائي\n"
            "✅ شروط إشارات مرنة ومحسنة\n"
            "✅ سجل وتقرير أداء\n\n"
            "اختر من القائمة أدناه:",
            kb_main())

def on_cb(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "القائمة الرئيسية", kb_main())
    elif data == "SCANALL":
        edit(cid, mid, "🔍 جاري الفحص... انتظر قليلاً")
        do_scan(chat_id=cid)
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر زوج العملات:", kb_pairs())
    elif data == "REPORT":
        edit(cid, mid, get_report(), kb_main())
    elif data == "RISK":
        s,p,l,b = calc_risk(10)
        edit(cid, mid,
            f"💰 <b>حاسبة المخاطرة</b>\n\n"
            f"الرصيد: ${BALANCE}\n"
            f"نسبة المخاطرة: {RISK_PCT}%\n"
            f"قيمة الصفقة: ${s}\n"
            f"الربح المتوقع: +${p}\n"
            f"الخسارة المحتملة: -${l}",
            kb_main())
    elif data.startswith("P:"):
        pair = data[2:]
        edit(cid, mid, f"⏱ اختر الإطار الزمني لـ {otc(pair)}", kb_tf(pair))
    elif data.startswith("T:"):
        _, pair, tf = data.split(":")
        edit(cid, mid, "🔍 جاري التحليل...")
        do_scan(chat_id=cid, pair=pair, tfk=tf)

# ══ استقبال الرسائل ════════════════════════════════════════
def polling():
    last = 0
    while True:
        try:
            res = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last+1}&timeout=15").json()
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

# ══ التشغيل ══════════════════════════════════════════════════
if __name__ == "__main__":
    threading.Thread(target=polling, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
