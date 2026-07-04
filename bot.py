import os, time, requests, threading, logging
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ══════════════════════════════════════════════════════════════
#  إعدادات — ضع التوكن في Render Environment Variables
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID   = int(os.environ.get("CHAT_ID", "8674500253"))

SYMBOLS = {
    "🇪🇺 EUR/USD": "EURUSD=X", "🇬🇧 GBP/USD": "GBPUSD=X",
    "🇯🇵 USD/JPY": "USDJPY=X", "🇨🇭 USD/CHF": "USDCHF=X",
    "🇨🇦 USD/CAD": "USDCAD=X", "🇦🇺 AUD/USD": "AUDUSD=X",
    "🇪🇺 EUR/GBP": "EURGBP=X", "🇪🇺 EUR/JPY": "EURJPY=X",
    "🇬🇧 GBP/JPY": "GBPJPY=X", "🇦🇺 AUD/CAD": "AUDCAD=X",
    "🇦🇺 AUD/CHF": "AUDCHF=X", "🇨🇦 CAD/CHF": "CADCHF=X",
    "🇨🇦 CAD/JPY": "CADJPY=X", "🇨🇭 CHF/JPY": "CHFJPY=X",
    "🇬🇧 GBP/AUD": "GBPAUD=X", "🥇 XAU/USD":  "GC=F",
}

TIMEFRAMES = {
    "1د":  {"interval":"1m",  "period":"1d",  "label":"1 دقيقة",  "bars":60},
    "5د":  {"interval":"5m",  "period":"5d",  "label":"5 دقائق",  "bars":60},
    "15د": {"interval":"15m", "period":"10d", "label":"15 دقيقة", "bars":60},
    "30د": {"interval":"30m", "period":"15d", "label":"30 دقيقة", "bars":60},
    "1س":  {"interval":"60m", "period":"30d", "label":"ساعة",     "bars":60},
}

AUTO_TFS       = ["5د", "15د"]
AUTO_INTERVAL  = 420    # كل 7 دقائق
COOLDOWN       = 600    # 10 دقائق بين نفس الإشارة
MIN_SCORE      = 9
FETCH_DELAY    = 2.5

last_sig  = {}
lock      = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ══ Flask ══════════════════════════════════════════════════════
app = Flask(__name__)
@app.route("/")
def home(): return "✅ بوت التداول الاحترافي يعمل"

# ══ Telegram ═══════════════════════════════════════════════════
def tg(method, data):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data, timeout=15)
        return r.json()
    except: return {"ok": False}

def send(cid, txt, kb=None):
    p = {"chat_id":cid,"text":txt,"parse_mode":"HTML","disable_web_page_preview":True}
    if kb: p["reply_markup"] = kb
    for _ in range(4):
        if tg("sendMessage",p).get("ok"): return True
        p["parse_mode"]=""
        time.sleep(1.5)
    return False

def edit(cid, mid, txt, kb=None):
    p = {"chat_id":cid,"message_id":mid,"text":txt,"parse_mode":"HTML"}
    if kb: p["reply_markup"] = kb
    tg("editMessageText", p)

def answer(cbid): tg("answerCallbackQuery",{"callback_query_id":cbid})

# ══ لوحات الأزرار ══════════════════════════════════════════════
def kb_main():
    return {"inline_keyboard":[
        [{"text":"🔍 فحص الكل الآن","callback_data":"SCANALL"}],
        [{"text":"📊 تحليل زوج محدد","callback_data":"PAIRS"}],
        [{"text":"📈 أقوى إشارة الآن","callback_data":"BEST"}],
        [{"text":"⚙️ الإعدادات","callback_data":"SETTINGS"},
         {"text":"❓ مساعدة","callback_data":"HELP"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows  = []
    for i in range(0, len(names), 2):
        rows.append([{"text":p,"callback_data":f"P:{p}"} for p in names[i:i+2]])
    rows.append([{"text":"🔙 رجوع","callback_data":"MAIN"}])
    return {"inline_keyboard": rows}

def kb_tf(pair):
    tfs  = list(TIMEFRAMES.items())
    rows = []
    for i in range(0, len(tfs), 3):
        rows.append([{"text":v["label"],"callback_data":f"T:{pair}:{k}"} for k,v in tfs[i:i+3]])
    rows.append([{"text":"🔙 رجوع","callback_data":"PAIRS"}])
    return {"inline_keyboard": rows}

def kb_back():
    return {"inline_keyboard":[
        [{"text":"🔍 فحص الكل","callback_data":"SCANALL"},
         {"text":"📊 تحليل زوج","callback_data":"PAIRS"}],
        [{"text":"📈 أقوى إشارة","callback_data":"BEST"},
         {"text":"🏠 القائمة","callback_data":"MAIN"}],
    ]}

# ══ جلب البيانات ════════════════════════════════════════════════
def fetch(ticker, interval, period, min_bars=60):
    for attempt in range(4):
        try:
            if attempt > 0: time.sleep(20 * attempt)
            df = yf.download(ticker, interval=interval, period=period,
                             progress=False, auto_adjust=True, group_by="column")
            if df is None or df.empty: continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            needed = {"open","high","low","close"}
            if not needed.issubset(set(df.columns)): continue
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

# ══ المؤشرات ════════════════════════════════════════════════════
def EMA(s,p): return s.ewm(span=p,adjust=False).mean()

def RSI(s,p=14):
    d=s.diff()
    g=d.clip(lower=0).ewm(alpha=1/p,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/p,adjust=False).mean()
    return 100-100/(1+g/(l+1e-9))

def MACD(s):
    f=s.ewm(span=12,adjust=False).mean()
    sl=s.ewm(span=26,adjust=False).mean()
    ln=f-sl; sg=ln.ewm(span=9,adjust=False).mean()
    return ln,sg,ln-sg

def BB(s,p=20):
    m=s.rolling(p).mean(); std=s.rolling(p).std(ddof=0)
    return m+2*std,m,m-2*std

def ATR(h,l,c,p=14):
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=1).mean()

def ADX(h,l,c,p=14):
    up=h.diff(); down=-l.diff()
    pdm=np.where((up>down)&(up>0),up,0.0)
    ndm=np.where((down>up)&(down>0),down,0.0)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr_=tr.rolling(p).mean()
    pdi=100*pd.Series(pdm,index=h.index).rolling(p).mean()/(atr_+1e-9)
    ndi=100*pd.Series(ndm,index=h.index).rolling(p).mean()/(atr_+1e-9)
    dx=100*(pdi-ndi).abs()/(pdi+ndi+1e-9)
    return dx.rolling(p).mean(),pdi,ndi

def STOCH(h,l,c,k=14,d=3):
    lk=l.rolling(k).min(); hk=h.rolling(k).max()
    sk=100*(c-lk)/(hk-lk+1e-9)
    return sk,sk.rolling(d).mean()

def PIVOT(df):
    h=float(df["high"].tail(20).max())
    l=float(df["low"].tail(20).min())
    c=float(df["close"].iloc[-1])
    pv=(h+l+c)/3
    return round(pv-(h-l),5),round(2*pv-h,5),round(pv,5),round(2*pv-l,5),round(pv+(h-l),5)

# ══ الشموع اليابانية ════════════════════════════════════════════
def CANDLES(df):
    res={"buy":[],"sell":[]}
    if len(df)<5: return res
    O=df["open"].values; H=df["high"].values
    L=df["low"].values;  C=df["close"].values
    def body(i):  return abs(C[i]-O[i])
    def rng(i):   return H[i]-L[i]+1e-9
    def bull(i):  return C[i]>O[i]
    def bear(i):  return C[i]<O[i]
    def uw(i):    return H[i]-max(C[i],O[i])
    def lw(i):    return min(C[i],O[i])-L[i]
    i=-1
    # صعودية
    if bull(i) and body(i)>0 and lw(i)>=2*body(i) and uw(i)<=body(i)*0.3:
        res["buy"].append("🔨 مطرقة")
    if bear(-2) and bull(i) and O[i]<=C[-2] and C[i]>=O[-2] and body(i)>body(-2):
        res["buy"].append("🕯 ابتلاع↑")
    if body(i)>0 and lw(i)>=2.5*body(i) and uw(i)<=body(i)*0.4:
        res["buy"].append("📌 بين بار↑")
    if bull(-3) and bull(-2) and bull(i) and C[i]>C[-2]>C[-3] and O[i]>O[-2]>O[-3]:
        res["buy"].append("⚔️ 3 جنود")
    if bear(-3) and body(-2)<rng(-2)*0.25 and bull(i) and C[i]>(O[-3]+C[-3])/2:
        res["buy"].append("⭐ نجمة الصباح")
    if bear(-2) and bull(i) and abs(L[i]-L[-2])<rng(i)*0.05:
        res["buy"].append("🔧 قاع توأم")
    # هبوطية
    if bear(i) and body(i)>0 and uw(i)>=2*body(i) and lw(i)<=body(i)*0.3:
        res["sell"].append("💫 نجمة ساقطة")
    if bull(-2) and bear(i) and O[i]>=C[-2] and C[i]<=O[-2] and body(i)>body(-2):
        res["sell"].append("🕯 ابتلاع↓")
    if body(i)>0 and uw(i)>=2.5*body(i) and lw(i)<=body(i)*0.4:
        res["sell"].append("📌 بين بار↓")
    if bear(-3) and bear(-2) and bear(i) and C[i]<C[-2]<C[-3] and O[i]<O[-2]<O[-3]:
        res["sell"].append("🦅 3 غربان")
    if bull(-3) and body(-2)<rng(-2)*0.25 and bear(i) and C[i]<(O[-3]+C[-3])/2:
        res["sell"].append("🌙 نجمة المساء")
    if bull(-2) and bear(i) and abs(H[i]-H[-2])<rng(i)*0.05:
        res["sell"].append("🔧 قمة توأم")
    return res

# ══ محرك التحليل الاحترافي ══════════════════════════════════════
def analyze(df):
    if df is None or df.empty or len(df)<50: return None,0,{}
    cl=df["close"].astype(float)
    hi=df["high"].astype(float)
    lo=df["low"].astype(float)

    e8=float(EMA(cl,8).iloc[-1]);   e21=float(EMA(cl,21).iloc[-1])
    e50=float(EMA(cl,50).iloc[-1]); e200=float(EMA(cl,200).iloc[-1]) if len(cl)>=200 else e50
    rv=float(RSI(cl).iloc[-1]);     rvp=float(RSI(cl).iloc[-2])
    ml,ms,mh=MACD(cl)
    mlv=float(ml.iloc[-1]); msv=float(ms.iloc[-1])
    mlp=float(ml.iloc[-2]); msp=float(ms.iloc[-2])
    mhv=float(mh.iloc[-1]); mhp=float(mh.iloc[-2])
    bu,bm,bl=BB(cl)
    buv=float(bu.iloc[-1]); bmv=float(bm.iloc[-1]); blv=float(bl.iloc[-1])
    av=max(float(ATR(hi,lo,cl).iloc[-1]),0.00005)
    sk,sd=STOCH(hi,lo,cl)
    skv=float(sk.iloc[-1]); sdv=float(sd.iloc[-1])
    skp=float(sk.iloc[-2]); sdp=float(sd.iloc[-2])
    adxs,pdis,ndis=ADX(hi,lo,cl)
    adxv=float(adxs.iloc[-1]); pdiv=float(pdis.iloc[-1]); ndiv=float(ndis.iloc[-1])
    s2,s1,pvt,r1,r2=PIVOT(df)
    p=float(cl.iloc[-1])
    bb_w=(buv-blv)/(bmv+1e-9)

    buy=sell=0; rb=[]; rs=[]

    # 1. EMA متعدد الطبقات
    if e8>e21>e50>e200:   buy+=5;  rb.append("EMA صاعد قوي جداً")
    elif e8>e21>e50:      buy+=4;  rb.append("EMA صاعد")
    elif e8>e21:          buy+=2;  rb.append("EMA صاعد جزئي")
    elif e8<e21<e50<e200: sell+=5; rs.append("EMA هابط قوي جداً")
    elif e8<e21<e50:      sell+=4; rs.append("EMA هابط")
    elif e8<e21:          sell+=2; rs.append("EMA هابط جزئي")

    # 2. RSI ذكي
    if rv<25:                          buy+=4;  rb.append(f"RSI ذروة بيع قوية {rv:.0f}")
    elif rv<35:                        buy+=3;  rb.append(f"RSI ذروة بيع {rv:.0f}")
    elif rv<45 and rv>rvp:             buy+=2;  rb.append(f"RSI صاعد {rv:.0f}")
    elif rv>75:                        sell+=4; rs.append(f"RSI ذروة شراء قوية {rv:.0f}")
    elif rv>65:                        sell+=3; rs.append(f"RSI ذروة شراء {rv:.0f}")
    elif rv>55 and rv<rvp:             sell+=2; rs.append(f"RSI هابط {rv:.0f}")

    # 3. MACD تقاطع ومومنتوم
    if mlp<msp and mlv>msv:            buy+=4;  rb.append("MACD تقاطع↑")
    elif mhv>0 and mhv>mhp:            buy+=2;  rb.append("MACD مومنتوم↑")
    if mlp>msp and mlv<msv:            sell+=4; rs.append("MACD تقاطع↓")
    elif mhv<0 and mhv<mhp:            sell+=2; rs.append("MACD مومنتوم↓")

    # 4. Bollinger Bands مع العرض
    if p<=blv and bb_w>0.005:          buy+=3;  rb.append("BB حد سفلي+ضغط")
    elif p<=blv:                       buy+=2;  rb.append("BB حد سفلي")
    elif p<bmv:                        buy+=1;  rb.append("BB تحت الوسط")
    if p>=buv and bb_w>0.005:          sell+=3; rs.append("BB حد علوي+ضغط")
    elif p>=buv:                       sell+=2; rs.append("BB حد علوي")
    elif p>bmv:                        sell+=1; rs.append("BB فوق الوسط")

    # 5. Stochastic تقاطع وذروة
    if skv<15 and sdv<15:              buy+=3;  rb.append(f"Stoch ذروة بيع {skv:.0f}")
    elif skv<25 and sdv<25:            buy+=2;  rb.append(f"Stoch تشبع بيع {skv:.0f}")
    if skp<sdp and skv>sdv and skv<40: buy+=2;  rb.append("Stoch تقاطع↑")
    if skv>85 and sdv>85:              sell+=3; rs.append(f"Stoch ذروة شراء {skv:.0f}")
    elif skv>75 and sdv>75:            sell+=2; rs.append(f"Stoch تشبع شراء {skv:.0f}")
    if skp>sdp and skv<sdv and skv>60: sell+=2; rs.append("Stoch تقاطع↓")

    # 6. ADX قوة الاتجاه
    if adxv>30:
        if pdiv>ndiv:                  buy+=3;  rb.append(f"ADX قوي جداً↑ {adxv:.0f}")
        else:                          sell+=3; rs.append(f"ADX قوي جداً↓ {adxv:.0f}")
    elif adxv>20:
        if pdiv>ndiv:                  buy+=2;  rb.append(f"ADX قوي↑ {adxv:.0f}")
        else:                          sell+=2; rs.append(f"ADX قوي↓ {adxv:.0f}")

    # 7. Pivot Points
    if abs(p-s1)<av*1.2:               buy+=3;  rb.append(f"دعم S1={s1}")
    elif abs(p-s2)<av*1.2:             buy+=3;  rb.append(f"دعم S2={s2}")
    elif abs(p-pvt)<av*0.8 and buy>sell: buy+=1; rb.append(f"Pivot={pvt}")
    if abs(p-r1)<av*1.2:               sell+=3; rs.append(f"مقاومة R1={r1}")
    elif abs(p-r2)<av*1.2:             sell+=3; rs.append(f"مقاومة R2={r2}")
    elif abs(p-pvt)<av*0.8 and sell>buy: sell+=1; rs.append(f"Pivot={pvt}")

    # 8. الشموع اليابانية
    cds=CANDLES(df)
    w={"🔨 مطرقة":3,"🕯 ابتلاع↑":4,"📌 بين بار↑":3,"⚔️ 3 جنود":5,
       "⭐ نجمة الصباح":4,"🔧 قاع توأم":3,
       "💫 نجمة ساقطة":3,"🕯 ابتلاع↓":4,"📌 بين بار↓":3,"🦅 3 غربان":5,
       "🌙 نجمة المساء":4,"🔧 قمة توأم":3}
    for nm in cds["buy"]:   buy+=w.get(nm,2);  rb.append(nm)
    for nm in cds["sell"]:  sell+=w.get(nm,2); rs.append(nm)

    # ══ فلاتر احترافية ══
    if buy>sell and e8<e21 and e21<e50: return None,0,{}
    if sell>buy and e8>e21 and e21>e50: return None,0,{}
    if buy>sell and rv>80:              return None,0,{}
    if sell>buy and rv<20:              return None,0,{}
    if max(buy,sell)<12 and adxv<15:    return None,0,{}

    score=max(buy,sell)
    if score<MIN_SCORE: return None,0,{}
    total=buy+sell
    conf=int((score/total)*100) if total else 0

    if score>=25:   stars="⭐⭐⭐⭐⭐"; grade="ممتاز جداً"
    elif score>=20: stars="⭐⭐⭐⭐";  grade="ممتاز"
    elif score>=15: stars="⭐⭐⭐";   grade="جيد جداً"
    elif score>=12: stars="⭐⭐";    grade="جيد"
    else:           stars="⭐";     grade="متوسط"

    sl_m=1.2 if score>=18 else 1.5
    tp_m=2.5 if score>=18 else 2.0
    rr=f"1 : {tp_m/sl_m:.2f}"

    det={
        "price":round(p,5),"rsi":round(rv,1),"adx":round(adxv,1),"atr":round(av,5),
        "bbl":round(blv,5),"bbu":round(buv,5),"pvt":pvt,"s1":s1,"r1":r1,
        "sl_b":round(p-sl_m*av,5),"tp_b":round(p+tp_m*av,5),
        "sl_s":round(p+sl_m*av,5),"tp_s":round(p-tp_m*av,5),
        "rr":rr,"stars":stars,"grade":grade,"score":score,"conf":conf,
    }
    if buy>sell:
        det["cds"]=" | ".join(cds["buy"]) if cds["buy"] else "—"
        det["why"]=" | ".join(rb)
        return "🟢 شراء",score,det
    if sell>buy:
        det["cds"]=" | ".join(cds["sell"]) if cds["sell"] else "—"
        det["why"]=" | ".join(rs)
        return "🔴 بيع",score,det
    return None,0,{}

# ══ تنسيق الرسالة ═══════════════════════════════════════════════
def fmt(name, tf_label, sig, det):
    ib = "شراء" in sig
    sl = det["sl_b"] if ib else det["sl_s"]
    tp = det["tp_b"] if ib else det["tp_s"]
    emoji = "📈" if ib else "📉"
    color = "🟢" if ib else "🔴"
    return (
        f"{emoji} <b>{name}</b> | <i>{tf_label}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{color} <b>{sig}</b>  {det['stars']}\n"
        f"🏆 الجودة: <b>{det['grade']}</b> | نقاط: <code>{det['score']}</code>\n"
        f"🎯 الثقة: <b>{det['conf']}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 السعر الحالي: <code>{det['price']}</code>\n"
        f"🛑 وقف الخسارة: <code>{sl}</code>\n"
        f"✅ هدف الربح:   <code>{tp}</code>\n"
        f"⚖️ نسبة R:R:    <code>{det['rr']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 RSI: <code>{det['rsi']}</code> | ADX: <code>{det['adx']}</code> | ATR: <code>{det['atr']}</code>\n"
        f"📊 BB:  <code>{det['bbl']}</code> ↔ <code>{det['bbu']}</code>\n"
        f"🏛 S1: <code>{det['s1']}</code> | Pivot: <code>{det['pvt']}</code> | R1: <code>{det['r1']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕯 شموع: <i>{det['cds']}</i>\n"
        f"📋 أسباب: <i>{det['why']}</i>\n"
        f"🕒 <i>{datetime.now().strftime('%H:%M:%S')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>للتحليل فقط — ليست نصيحة مالية</i>"
    )

# ══ الفحص ═══════════════════════════════════════════════════════
def do_scan(chat_id=None, pair=None, tfk=None):
    found=[]; best=None; best_score=0
    pairs = {pair:SYMBOLS[pair]} if pair else SYMBOLS
    tfs   = {tfk:TIMEFRAMES[tfk]} if tfk else {k:TIMEFRAMES[k] for k in AUTO_TFS}

    for name,ticker in pairs.items():
        time.sleep(FETCH_DELAY)
        for tk,tcfg in tfs.items():
            df=fetch(ticker,tcfg["interval"],tcfg["period"],tcfg["bars"])
            sig,score,det=analyze(df)
            if not sig: continue
            if chat_id is None:
                key=f"{name}|{tk}|{sig}"
                with lock:
                    if time.time()-last_sig.get(key,0)<COOLDOWN: continue
                    last_sig[key]=time.time()
            found.append((name,tcfg["label"],sig,score,det))
            if score>best_score:
                best_score=score; best=(name,tcfg["label"],sig,det)

    tgt=chat_id if chat_id else CHAT_ID
    for name,tf_label,sig,score,det in found:
        send(tgt, fmt(name,tf_label,sig,det), kb_back())
        time.sleep(1)
    return found, best

def auto_scan():
    time.sleep(15)
    while True:
        try:
            logging.info("🔍 فحص تلقائي...")
            do_scan()
        except Exception as e:
            logging.error(f"auto_scan: {e}")
        time.sleep(AUTO_INTERVAL)

# ══ الأزرار ═════════════════════════════════════════════════════
def on_cb(cid, mid, cbid, data):
    answer(cbid)

    if data=="MAIN":
        edit(cid,mid,
            "🤖 <b>بوت التداول الاحترافي</b>\n\n"
            f"🔄 فحص تلقائي كل <code>{AUTO_INTERVAL//60}</code> دقائق\n"
            f"📊 <code>{len(SYMBOLS)}</code> زوج | <code>{len(TIMEFRAMES)}</code> أطر زمنية",
            kb_main())

    elif data=="SCANALL":
        edit(cid,mid,"🔍 <b>جاري فحص كل الأزواج...</b>\n<i>انتظر دقيقتين</i>")
        found,_=do_scan(chat_id=cid)
        if not found:
            send(cid,"⚪ <b>لا توجد إشارات مؤكدة الآن</b>\n<i>البوت يراقب تلقائياً</i>",kb_main())

    elif data=="BEST":
        edit(cid,mid,"🔍 <b>جاري البحث عن أقوى إشارة...</b>")
        found,best=do_scan(chat_id=cid)
        if not best:
            send(cid,"⚪ <b>لا توجد إشارة قوية الآن</b>",kb_main())

    elif data=="SETTINGS":
        edit(cid,mid,
            f"⚙️ <b>الإعدادات:</b>\n\n"
            f"⏱ الفحص التلقائي: كل <code>{AUTO_INTERVAL//60}</code> دقائق\n"
            f"🎯 الحد الأدنى: <code>{MIN_SCORE}</code> نقطة\n"
            f"🔕 كولداون: <code>{COOLDOWN//60}</code> دقائق\n"
            f"📊 الأزواج: <code>{len(SYMBOLS)}</code>\n"
            f"⏰ الأطر التلقائية: <code>{', '.join(AUTO_TFS)}</code>",
            kb_main())

    elif data=="HELP":
        edit(cid,mid,
            "❓ <b>دليل البوت الاحترافي</b>\n\n"
            "<b>🔍 فحص الكل:</b> يفحص كل الأزواج ويرسل الإشارات\n"
            "<b>📈 أقوى إشارة:</b> يعطيك أفضل فرصة الآن\n"
            "<b>📊 تحليل زوج:</b> اختر زوج وإطار زمني تريده\n\n"
            "<b>المؤشرات (8 مؤشرات):</b>\n"
            "• EMA 8/21/50/200 — الاتجاه\n"
            "• RSI — ذروة الشراء والبيع\n"
            "• MACD — الزخم والتقاطعات\n"
            "• Bollinger Bands — حدود السعر\n"
            "• Stochastic — توقيت الدخول\n"
            "• ADX — قوة الاتجاه\n"
            "• Pivot Points — الدعم والمقاومة\n"
            "• 12 نمط شمعة يابانية\n\n"
            "<b>تقييم الجودة:</b>\n"
            "⭐ متوسط | ⭐⭐ جيد | ⭐⭐⭐ جيد جداً\n"
            "⭐⭐⭐⭐ ممتاز | ⭐⭐⭐⭐⭐ ممتاز جداً\n\n"
            "💡 <b>نصيحة:</b> تداول فقط على ⭐⭐⭐ وما فوق",
            kb_main())

    elif data=="PAIRS":
        edit(cid,mid,"📊 <b>اختر زوج العملات:</b>",kb_pairs())

    elif data.startswith("P:"):
        pair=data[2:]
        edit(cid,mid,f"⏱ <b>اختر الإطار الزمني لـ {pair}:</b>",kb_tf(pair))

    elif data.startswith("T:"):
        _,pair,tfk=data.split(":",2)
        tcfg=TIMEFRAMES.get(tfk)
        edit(cid,mid,f"🔍 <b>جاري تحليل {pair} | {tcfg['label']}...</b>")
        ticker=SYMBOLS.get(pair)
        if not ticker or not tcfg:
            send(cid,"❌ خطأ",kb_main()); return
        df=fetch(ticker,tcfg["interval"],tcfg["period"],tcfg["bars"])
        sig,score,det=analyze(df)
        if sig:
            send(cid,fmt(pair,tcfg["label"],sig,det),kb_back())
        else:
            send(cid,
                f"📊 <b>{pair}</b> | {tcfg['label']}\n\n"
                f"⚪ لا توجد إشارة مؤكدة الآن\n"
                f"<i>جرب إطاراً آخر أو انتظر الفحص التلقائي</i>",
                kb_back())

# ══ الأوامر ═════════════════════════════════════════════════════
def on_cmd(cid, txt):
    if txt.strip() in ["/start","/menu"]:
        send(cid,
            "🤖 <b>بوت التداول الاحترافي v2.0</b>\n\n"
            "✅ 8 مؤشرات فنية متقدمة\n"
            "✅ 12 نمط شمعة يابانية\n"
            "✅ Pivot Points دعم ومقاومة\n"
            "✅ وقف خسارة وهدف ربح ذكي\n"
            "✅ تقييم الجودة بالنجوم\n"
            "✅ فحص تلقائي كل 7 دقائق\n"
            f"✅ {len(SYMBOLS)} زوج عملات\n\n"
            "اختر من القائمة:",
            kb_main())

# ══ Polling ════════════════════════════════════════════════════
def polling():
    last_id=0
    while True:
        try:
            res=requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_id+1}&timeout=10",
                timeout=15).json()
            for upd in res.get("result",[]):
                last_id=upd["update_id"]
                if "message" in upd:
                    msg=upd["message"]
                    cid=msg.get("chat",{}).get("id")
                    txt=msg.get("text","")
                    if cid and txt:
                        threading.Thread(target=on_cmd,args=(cid,txt),daemon=True).start()
                elif "callback_query" in upd:
                    cb=upd["callback_query"]
                    cid=cb["message"]["chat"]["id"]
                    mid=cb["message"]["message_id"]
                    cbid=cb["id"]
                    data=cb.get("data","")
                    threading.Thread(target=on_cb,args=(cid,mid,cbid,data),daemon=True).start()
        except Exception as e:
            logging.error(f"polling: {e}")
            time.sleep(3)

# ══ Main ════════════════════════════════════════════════════════
if __name__=="__main__":
    threading.Thread(target=auto_scan,daemon=True).start()
    threading.Thread(target=polling,  daemon=True).start()
    port=int(os.environ.get("PORT",8080))
    logging.info(f"✅ البوت الاحترافي v2.0 اشتغل على port {port}")
    try:
        from waitress import serve
        serve(app,host="0.0.0.0",port=port)
    except ImportError:
        app.run(host="0.0.0.0",port=port)
