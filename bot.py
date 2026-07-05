import os, time, requests, threading, logging
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

# ══════════════════════════════════════════════════════════════
#  إعدادات
# ══════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID   = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE   = float(os.environ.get("BALANCE", "1000"))  # رصيد افتراضي
RISK_PCT  = float(os.environ.get("RISK_PCT", "2"))     # نسبة المخاطرة %

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

# OTC suffix للعرض
def otc(name): return f"{name} OTC"

TIMEFRAMES = {
    "1د":  {"interval":"1m",  "period":"1d",  "label":"1 دقيقة",  "bars":60,  "exp":"01:00"},
    "5د":  {"interval":"5m",  "period":"5d",  "label":"5 دقائق",  "bars":60,  "exp":"05:00"},
    "15د": {"interval":"15m", "period":"10d", "label":"15 دقيقة", "bars":60,  "exp":"15:00"},
    "30د": {"interval":"30m", "period":"15d", "label":"30 دقيقة", "bars":60,  "exp":"30:00"},
    "1س":  {"interval":"60m", "period":"30d", "label":"ساعة",     "bars":60,  "exp":"01:00:00"},
}

AUTO_TFS      = ["5د","15د"]
AUTO_INTERVAL = 420
COOLDOWN      = 600
MIN_SCORE     = 7
FETCH_DELAY   = 2.5

last_sig = {}
lock     = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ══ Flask ══════════════════════════════════════════════════════
app = Flask(__name__)
@app.route("/")
def home(): return "✅ BASILISK BOT • LIVE"

# ══ Telegram ═══════════════════════════════════════════════════
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
        p["parse_mode"]=""
        time.sleep(1.5)
    return False

def edit(cid, mid, txt, kb=None):
    p = {"chat_id":cid,"message_id":mid,"text":txt,"parse_mode":"HTML"}
    if kb: p["reply_markup"] = kb
    tg("editMessageText",p)

def answer(cbid): tg("answerCallbackQuery",{"callback_query_id":cbid})

# ══ لوحات الأزرار — Basilisk Style ════════════════════════════
def kb_main():
    return {"inline_keyboard":[
        [{"text":"⚡ SCAN — فحص الكل","callback_data":"SCANALL"}],
        [{"text":"📊 اختر زوج","callback_data":"PAIRS"},
         {"text":"🏆 أقوى إشارة","callback_data":"BEST"}],
        [{"text":"💰 Risk Calculator","callback_data":"RISK"},
         {"text":"⚙️ الإعدادات","callback_data":"SETTINGS"}],
        [{"text":"❓ مساعدة","callback_data":"HELP"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows  = []
    for i in range(0, len(names), 2):
        rows.append([{"text":f"● {p} OTC","callback_data":f"P:{p}"} for p in names[i:i+2]])
    rows.append([{"text":"◄ رجوع","callback_data":"MAIN"}])
    return {"inline_keyboard":rows}

def kb_tf(pair):
    tfs  = list(TIMEFRAMES.items())
    rows = []
    for i in range(0, len(tfs), 3):
        rows.append([{"text":v["exp"],"callback_data":f"T:{pair}:{k}"} for k,v in tfs[i:i+3]])
    rows.append([{"text":"◄ رجوع","callback_data":"PAIRS"}])
    return {"inline_keyboard":rows}

def kb_result():
    return {"inline_keyboard":[
        [{"text":"⚡ SCAN مجدداً","callback_data":"SCANALL"},
         {"text":"🏆 أقوى إشارة","callback_data":"BEST"}],
        [{"text":"📊 زوج آخر","callback_data":"PAIRS"},
         {"text":"🏠 القائمة","callback_data":"MAIN"}],
    ]}

# ══ Risk Calculator ════════════════════════════════════════════
def calc_risk(score, payout=85):
    """حساب حجم الصفقة والربح المتوقع"""
    stake = round(BALANCE * (RISK_PCT/100), 2)
    profit = round(stake * (payout/100), 2)
    loss   = stake
    # Break-even win rate
    bew    = round(100/(1+(payout/100)), 1)
    return stake, profit, loss, bew

# ══ جلب البيانات ════════════════════════════════════════════════
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
            df = df[df["high"]!=df["low"]]
            if len(df)<min_bars: continue
            return df.copy()
        except Exception as e:
            err=str(e)
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
    if bull(i) and body(i)>0 and lw(i)>=2*body(i) and uw(i)<=body(i)*0.3:
        res["buy"].append("Pin Bar ↑")
    if bear(-2) and bull(i) and O[i]<=C[-2] and C[i]>=O[-2] and body(i)>body(-2):
        res["buy"].append("Engulfing ↑")
    if body(i)>0 and lw(i)>=2.5*body(i) and uw(i)<=body(i)*0.4:
        res["buy"].append("Pin Bar ↑")
    if bull(-3) and bull(-2) and bull(i) and C[i]>C[-2]>C[-3]:
        res["buy"].append("3 Soldiers ↑")
    if bear(-3) and body(-2)<rng(-2)*0.25 and bull(i) and C[i]>(O[-3]+C[-3])/2:
        res["buy"].append("Morning Star ↑")
    if bear(-2) and bull(i) and abs(L[i]-L[-2])<rng(i)*0.05:
        res["buy"].append("Tweezer Bottom ↑")
    if bear(i) and body(i)>0 and uw(i)>=2*body(i) and lw(i)<=body(i)*0.3:
        res["sell"].append("Pin Bar ↓")
    if bull(-2) and bear(i) and O[i]>=C[-2] and C[i]<=O[-2] and body(i)>body(-2):
        res["sell"].append("Engulfing ↓")
    if body(i)>0 and uw(i)>=2.5*body(i) and lw(i)<=body(i)*0.4:
        res["sell"].append("Pin Bar ↓")
    if bear(-3) and bear(-2) and bear(i) and C[i]<C[-2]<C[-3]:
        res["sell"].append("3 Crows ↓")
    if bull(-3) and body(-2)<rng(-2)*0.25 and bear(i) and C[i]<(O[-3]+C[-3])/2:
        res["sell"].append("Evening Star ↓")
    if bull(-2) and bear(i) and abs(H[i]-H[-2])<rng(i)*0.05:
        res["sell"].append("Tweezer Top ↓")
    return res

# ══ محرك التحليل ════════════════════════════════════════════════
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

    if e8>e21>e50>e200:   buy+=5;  rb.append("EMA Bullish Strong")
    elif e8>e21>e50:      buy+=4;  rb.append("EMA Bullish")
    elif e8>e21:          buy+=2;  rb.append("EMA Partial Bull")
    elif e8<e21<e50<e200: sell+=5; rs.append("EMA Bearish Strong")
    elif e8<e21<e50:      sell+=4; rs.append("EMA Bearish")
    elif e8<e21:          sell+=2; rs.append("EMA Partial Bear")

    if rv<25:                          buy+=4;  rb.append(f"RSI Oversold {rv:.0f}")
    elif rv<35:                        buy+=3;  rb.append(f"RSI Low {rv:.0f}")
    elif rv<45 and rv>rvp:             buy+=2;  rb.append(f"RSI Rising {rv:.0f}")
    elif rv>75:                        sell+=4; rs.append(f"RSI Overbought {rv:.0f}")
    elif rv>65:                        sell+=3; rs.append(f"RSI High {rv:.0f}")
    elif rv>55 and rv<rvp:             sell+=2; rs.append(f"RSI Falling {rv:.0f}")

    if mlp<msp and mlv>msv:            buy+=4;  rb.append("MACD Cross UP")
    elif mhv>0 and mhv>mhp:            buy+=2;  rb.append("MACD Momentum UP")
    if mlp>msp and mlv<msv:            sell+=4; rs.append("MACD Cross DOWN")
    elif mhv<0 and mhv<mhp:            sell+=2; rs.append("MACD Momentum DOWN")

    if p<=blv and bb_w>0.005:          buy+=3;  rb.append("BB Lower Band Bounce")
    elif p<=blv:                       buy+=2;  rb.append("BB Lower Band")
    elif p<bmv:                        buy+=1;  rb.append("BB Below Mid")
    if p>=buv and bb_w>0.005:          sell+=3; rs.append("BB Upper Band Reject")
    elif p>=buv:                       sell+=2; rs.append("BB Upper Band")
    elif p>bmv:                        sell+=1; rs.append("BB Above Mid")

    if skv<15 and sdv<15:              buy+=3;  rb.append(f"Stoch Oversold {skv:.0f}")
    elif skv<25 and sdv<25:            buy+=2;  rb.append(f"Stoch Low {skv:.0f}")
    if skp<sdp and skv>sdv and skv<40: buy+=2;  rb.append("Stoch Cross UP")
    if skv>85 and sdv>85:              sell+=3; rs.append(f"Stoch Overbought {skv:.0f}")
    elif skv>75 and sdv>75:            sell+=2; rs.append(f"Stoch High {skv:.0f}")
    if skp>sdp and skv<sdv and skv>60: sell+=2; rs.append("Stoch Cross DOWN")

    if adxv>30:
        if pdiv>ndiv:                  buy+=3;  rb.append(f"ADX Strong Trend UP {adxv:.0f}")
        else:                          sell+=3; rs.append(f"ADX Strong Trend DOWN {adxv:.0f}")
    elif adxv>20:
        if pdiv>ndiv:                  buy+=2;  rb.append(f"ADX Trend UP {adxv:.0f}")
        else:                          sell+=2; rs.append(f"ADX Trend DOWN {adxv:.0f}")

    if abs(p-s1)<av*1.2:               buy+=3;  rb.append(f"Support S1={s1}")
    elif abs(p-s2)<av*1.2:             buy+=3;  rb.append(f"Support S2={s2}")
    if abs(p-r1)<av*1.2:               sell+=3; rs.append(f"Resistance R1={r1}")
    elif abs(p-r2)<av*1.2:             sell+=3; rs.append(f"Resistance R2={r2}")

    cds=CANDLES(df)
    w={"Pin Bar ↑":3,"Engulfing ↑":4,"3 Soldiers ↑":5,"Morning Star ↑":4,
       "Tweezer Bottom ↑":3,"Pin Bar ↓":3,"Engulfing ↓":4,"3 Crows ↓":5,
       "Evening Star ↓":4,"Tweezer Top ↓":3}
    for nm in cds["buy"]:   buy+=w.get(nm,2);  rb.append(nm)
    for nm in cds["sell"]:  sell+=w.get(nm,2); rs.append(nm)

    # فلاتر معتدلة — لا نلغي إلا الحالات المتعارضة جداً
    if buy>sell and e8<e21 and e21<e50 and adxv<20: return None,0,{}
    if sell>buy and e8>e21 and e21>e50 and adxv<20: return None,0,{}
    if buy>sell and rv>85:              return None,0,{}
    if sell>buy and rv<15:              return None,0,{}

    score=max(buy,sell)
    if score<MIN_SCORE: return None,0,{}
    total=buy+sell
    conf=int((score/total)*100) if total else 0

    # قوة الإشارة بشكل Basilisk (من 100)
    power=min(100,int(score*2.5))

    if power>=85:   grade="STRONG";  bar="████████████" ; stars="⭐⭐⭐⭐⭐"
    elif power>=70: grade="MEDIUM+"; bar="█████████░░░" ; stars="⭐⭐⭐⭐"
    elif power>=55: grade="MEDIUM";  bar="███████░░░░░" ; stars="⭐⭐⭐"
    elif power>=40: grade="WEAK+";   bar="█████░░░░░░░" ; stars="⭐⭐"
    else:           grade="WEAK";    bar="███░░░░░░░░░" ; stars="⭐"

    sl_m=1.2 if power>=70 else 1.5
    tp_m=2.5 if power>=70 else 2.0

    det={
        "price":round(p,5),"rsi":round(rv,1),"adx":round(adxv,1),"atr":round(av,5),
        "bbl":round(blv,5),"bbu":round(buv,5),"pvt":pvt,"s1":s1,"r1":r1,
        "sl_b":round(p-sl_m*av,5),"tp_b":round(p+tp_m*av,5),
        "sl_s":round(p+sl_m*av,5),"tp_s":round(p-tp_m*av,5),
        "rr":f"1:{tp_m/sl_m:.2f}","stars":stars,"grade":grade,
        "score":score,"conf":conf,"power":power,"bar":bar,
    }
    if buy>sell:
        det["cds"]=" | ".join(cds["buy"]) if cds["buy"] else "—"
        det["why"]=" | ".join(rb)
        return "BUY",score,det
    if sell>buy:
        det["cds"]=" | ".join(cds["sell"]) if cds["sell"] else "—"
        det["why"]=" | ".join(rs)
        return "SELL",score,det
    return None,0,{}

def analyze_forced(df):
    """
    تحليل يدوي محسّن:
    - يعطي BUY/SELL فقط إذا فيه اتجاه واضح
    - يعطي WAIT إذا المؤشرات متعارضة
    - يشرح السبب دائماً
    """
    if df is None or df.empty or len(df)<30: return None,0,{}
    cl=df["close"].astype(float)
    hi=df["high"].astype(float)
    lo=df["low"].astype(float)

    e8=float(EMA(cl,8).iloc[-1]);   e21=float(EMA(cl,21).iloc[-1])
    e50=float(EMA(cl,50).iloc[-1])
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

    buy=sell=0; rb=[]; rs=[]

    # ══ 1. EMA — الاتجاه الرئيسي (وزن عالي) ══
    if e8>e21>e50:   buy+=5;  rb.append("EMA Bullish ✓")
    elif e8>e21:     buy+=2;  rb.append("EMA Partial Bull")
    elif e8<e21<e50: sell+=5; rs.append("EMA Bearish ✓")
    elif e8<e21:     sell+=2; rs.append("EMA Partial Bear")

    # ══ 2. RSI — ذروة الشراء والبيع ══
    if rv < 30:
        buy+=4; rb.append(f"RSI Oversold {rv:.0f} ✓")
    elif rv < 40:
        buy+=2; rb.append(f"RSI Low {rv:.0f}")
    elif rv > 70:
        sell+=4; rs.append(f"RSI Overbought {rv:.0f} ✓")
    elif rv > 60:
        sell+=2; rs.append(f"RSI High {rv:.0f}")
    # RSI 40-60 = محايد — لا نضيف نقاط

    # ══ 3. MACD — التقاطع أهم من الاتجاه ══
    if mlp<msp and mlv>msv:   buy+=4;  rb.append("MACD Cross UP ✓")
    elif mhv>0 and mhv>mhp:   buy+=1;  rb.append("MACD +")
    if mlp>msp and mlv<msv:   sell+=4; rs.append("MACD Cross DOWN ✓")
    elif mhv<0 and mhv<mhp:   sell+=1; rs.append("MACD -")

    # ══ 4. Bollinger Bands ══
    if p <= blv:   buy+=3;  rb.append("BB Lower ✓")
    elif p >= buv: sell+=3; rs.append("BB Upper ✓")

    # ══ 5. Stochastic ══
    if skv<20 and sdv<20:              buy+=3;  rb.append(f"Stoch Oversold {skv:.0f} ✓")
    elif skp<sdp and skv>sdv and skv<40: buy+=2; rb.append("Stoch Cross UP")
    if skv>80 and sdv>80:              sell+=3; rs.append(f"Stoch Overbought {skv:.0f} ✓")
    elif skp>sdp and skv<sdv and skv>60: sell+=2; rs.append("Stoch Cross DOWN")

    # ══ 6. ADX — يؤكد الاتجاه فقط إذا قوي ══
    if adxv > 25:
        if pdiv > ndiv: buy+=2;  rb.append(f"ADX Bull {adxv:.0f} ✓")
        else:           sell+=2; rs.append(f"ADX Bear {adxv:.0f} ✓")

    # ══ 7. Pivot Points ══
    if abs(p-s1) < av*1.2: buy+=2;  rb.append(f"Near Support S1")
    if abs(p-r1) < av*1.2: sell+=2; rs.append(f"Near Resistance R1")

    # ══ 8. الشموع ══
    cds=CANDLES(df)
    w={"Pin Bar ↑":3,"Engulfing ↑":4,"3 Soldiers ↑":5,"Morning Star ↑":4,
       "Tweezer Bottom ↑":3,"Pin Bar ↓":3,"Engulfing ↓":4,"3 Crows ↓":5,
       "Evening Star ↓":4,"Tweezer Top ↓":3}
    for nm in cds["buy"]:  buy+=w.get(nm,2);  rb.append(f"{nm} ✓")
    for nm in cds["sell"]: sell+=w.get(nm,2); rs.append(f"{nm} ✓")

    total = buy + sell
    if total == 0: return None,0,{}

    score = max(buy,sell)
    conf  = int((score/total)*100)
    diff  = abs(buy-sell)

    # ══ فلتر التعارض ══
    # إذا الفرق بين buy و sell صغير جداً → السوق متعارض → WAIT
    if diff < 3 and adxv < 30:
        return "WAIT", score, {
            "price":round(p,5),"rsi":round(rv,1),"adx":round(adxv,1),
            "atr":round(av,5),"bbl":round(blv,5),"bbu":round(buv,5),
            "pvt":pvt,"s1":s1,"r1":r1,
            "sl_b":0,"tp_b":0,"sl_s":0,"tp_s":0,
            "rr":"—","stars":"⚪","grade":"WAIT",
            "score":score,"conf":conf,"power":0,"bar":"░░░░░░░░░░░░",
            "cds":"—",
            "why":f"Buy:{buy} vs Sell:{sell} — متعارض | " + " | ".join(rb+rs)
        }

    power = min(100, int(score*2.5))
    if power>=85:   grade="STRONG";  bar="████████████"; stars="⭐⭐⭐⭐⭐"
    elif power>=70: grade="MEDIUM+"; bar="█████████░░░"; stars="⭐⭐⭐⭐"
    elif power>=55: grade="MEDIUM";  bar="███████░░░░░"; stars="⭐⭐⭐"
    elif power>=40: grade="WEAK+";   bar="█████░░░░░░░"; stars="⭐⭐"
    else:           grade="WEAK";    bar="███░░░░░░░░░"; stars="⭐"

    sl_m = 1.2 if power>=70 else 1.5
    tp_m = 2.5 if power>=70 else 2.0

    det={
        "price":round(p,5),"rsi":round(rv,1),"adx":round(adxv,1),"atr":round(av,5),
        "bbl":round(blv,5),"bbu":round(buv,5),"pvt":pvt,"s1":s1,"r1":r1,
        "sl_b":round(p-sl_m*av,5),"tp_b":round(p+tp_m*av,5),
        "sl_s":round(p+sl_m*av,5),"tp_s":round(p-tp_m*av,5),
        "rr":f"1:{tp_m/sl_m:.2f}","stars":stars,"grade":grade,
        "score":score,"conf":conf,"power":power,"bar":bar,
    }
    if buy > sell:
        det["cds"]=" | ".join(cds["buy"]) if cds["buy"] else "—"
        det["why"]=" | ".join(rb)
        return "BUY", score, det
    else:
        det["cds"]=" | ".join(cds["sell"]) if cds["sell"] else "—"
        det["why"]=" | ".join(rs)
        return "SELL", score, det

def fmt_neutral(name, tf_label, exp, df):
    """رسالة عندما البيانات ناقصة"""
    p = round(float(df["close"].iloc[-1]), 5) if not df.empty else "—"
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK</b> • {otc(name)}\n"
        f"⏱ {tf_label} | EXP: {exp}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>بيانات غير كافية</b>\n\n"
        f"💰 السعر: <code>{p}</code>\n"
        f"<i>البيانات قليلة لهذا الإطار الزمني\nجرب 5د أو 15د</i>"
    )

# ══ تنسيق Basilisk ══════════════════════════════════════════════
def fmt(name, tf_label, sig, det, exp="05:00", payout=85):
    is_buy = sig=="BUY"
    sl = det["sl_b"] if is_buy else det["sl_s"]
    tp = det["tp_b"] if is_buy else det["tp_s"]
    stake, profit, loss, bew = calc_risk(det["score"], payout)
    sig_emoji = "▲" if is_buy else "▼"
    sig_color = "🟢" if is_buy else "🔴"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK SIGNAL</b> • LIVE\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 PAIR:    <code>{otc(name)}</code>\n"
        f"💰 PAYOUT:  <code>{payout}%</code>\n"
        f"⏱ TIME:    <code>{exp}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"        {sig_emoji} <b>{sig_color} {sig}</b> {sig_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <code>{det['power']} / 100</code>  {det['bar']}\n"
        f"🏆 <b>{det['grade']}</b>  {det['stars']}\n"
        f"🎯 Confidence: <code>{det['conf']}%</code>\n"
        f"🕯 Pattern: <i>{det['cds']}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 RISK CALCULATOR\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance:        <code>${BALANCE:,.2f}</code>\n"
        f"⚠️ Risk {RISK_PCT}%:       <code>${stake}</code>\n"
        f"✅ Potential Profit: <code>+${profit}</code>\n"
        f"❌ Potential Loss:   <code>-${loss}</code>\n"
        f"📈 Break-even:     <code>≥ {bew}%</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 RSI:<code>{det['rsi']}</code> ADX:<code>{det['adx']}</code> ATR:<code>{det['atr']}</code>\n"
        f"📊 BB: <code>{det['bbl']}</code>↔<code>{det['bbu']}</code>\n"
        f"🏛 S1:<code>{det['s1']}</code> PVT:<code>{det['pvt']}</code> R1:<code>{det['r1']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <i>{det['why']}</i>\n"
        f"🕒 <i>{datetime.now().strftime('%H:%M:%S')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Not a trading recommendation</i>"
    )

def fmt_wait(name, tcfg, det):
    """رسالة WAIT — السوق متعارض"""
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK</b> • {otc(name)}\n"
        f"⏱ {tcfg['label']} | EXP: {tcfg['exp']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏸ <b>⚪ WAIT — انتظر</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 السوق متعارض الآن\n"
        f"💰 السعر: <code>{det['price']}</code>\n"
        f"📉 RSI: <code>{det['rsi']}</code> | ADX: <code>{det['adx']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <i>{det['why']}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>انتظر حتى تتضح الإشارة\nأو جرب إطاراً زمنياً آخر</i>"
    )

def fmt_no_signal(name, tcfg, df):
    """تنسيق رسالة NO SIGNAL مع تفاصيل المؤشرات"""
    if df is None or df.empty or len(df) < 20:
        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>BASILISK</b> • {otc(name)}\n"
            f"⏱ {tcfg['label']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚪ <b>NO SIGNAL</b>\n"
            f"<i>بيانات غير كافية</i>"
        )

    cl = df["close"].astype(float)
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)

    rv   = float(RSI(cl).iloc[-1])
    e8   = float(EMA(cl,8).iloc[-1])
    e21  = float(EMA(cl,21).iloc[-1])
    e50  = float(EMA(cl,50).iloc[-1])
    ml,ms,mh = MACD(cl)
    mhv  = float(mh.iloc[-1])
    av   = max(float(ATR(hi,lo,cl).iloc[-1]), 0.00005)
    p    = float(cl.iloc[-1])
    adxs,pdis,ndis = ADX(hi,lo,cl)
    adxv = float(adxs.iloc[-1])

    trend = "↑ صاعد" if e8>e21 else "↓ هابط" if e8<e21 else "→ محايد"
    rsi_s = "تشبع بيع" if rv<30 else "تشبع شراء" if rv>70 else "محايد"
    macd_s= "↑ إيجابي" if mhv>0 else "↓ سلبي"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>BASILISK</b> • {otc(name)}\n"
        f"⏱ {tcfg['label']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚪ <b>NO CLEAR SIGNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>تحليل السوق الآن:</b>\n"
        f"💰 السعر: <code>{round(p,5)}</code>\n"
        f"📈 الاتجاه: <code>{trend}</code>\n"
        f"📉 RSI: <code>{rv:.1f}</code> — {rsi_s}\n"
        f"📊 MACD: <code>{macd_s}</code>\n"
        f"💪 ADX: <code>{adxv:.1f}</code> — {'قوي' if adxv>25 else 'ضعيف'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ السوق يحتاج إشارة أقوى\n"
        f"<i>جرب إطاراً زمنياً آخر أو انتظر</i>"
    )

# ══ الفحص ═══════════════════════════════════════════════════════
def do_scan(chat_id=None, pair=None, tfk=None):
    """
    pair + tfk  → يدوي: زوج واحد + إطار واحد فقط
    لا شيء     → تلقائي: كل الأزواج بالأطر التلقائية
    """
    found=[]; best=None; best_score=0
    tgt = chat_id if chat_id else CHAT_ID

    # ── يدوي: زوج + إطار محدد ──
    if pair and tfk:
        ticker = SYMBOLS.get(pair)
        tcfg   = TIMEFRAMES.get(tfk)
        if not ticker or not tcfg:
            send(tgt, "❌ خطأ في البيانات", kb_main())
            return [], None

        df = fetch(ticker, tcfg["interval"], tcfg["period"], tcfg["bars"])

        # ✅ استخدم analyze_forced — يعطي نتيجة دائماً مع WAIT إذا متعارض
        sig, score, det = analyze_forced(df)

        if sig == "WAIT":
            send(tgt, fmt_wait(pair, tcfg, det), kb_result())
        elif sig in ("BUY","SELL"):
            send(tgt, fmt(pair, tcfg["label"], sig, det, tcfg["exp"]), kb_result())
        else:
            send(tgt, fmt_no_signal(pair, tcfg, df), kb_result())

        return [], None

    # ── تلقائي: كل الأزواج ──
    for name, ticker in SYMBOLS.items():
        time.sleep(FETCH_DELAY)
        for tk in AUTO_TFS:
            tcfg = TIMEFRAMES[tk]
            df   = fetch(ticker, tcfg["interval"], tcfg["period"], tcfg["bars"])
            sig, score, det = analyze(df)
            if not sig: continue

            key = f"{name}|{tk}|{sig}"
            with lock:
                if time.time() - last_sig.get(key, 0) < COOLDOWN: continue
                last_sig[key] = time.time()

            found.append((name, tcfg["label"], sig, score, det, tcfg["exp"]))
            if score > best_score:
                best_score = score
                best = (name, tcfg["label"], sig, det, tcfg["exp"])

    for name, tf_label, sig, score, det, exp in found:
        send(tgt, fmt(name, tf_label, sig, det, exp), kb_result())
        time.sleep(1)

    return found, best

def auto_scan():
    time.sleep(15)
    while True:
        try:
            logging.info("⚡ BASILISK SCAN...")
            do_scan()
        except Exception as e:
            logging.error(f"scan: {e}")
        time.sleep(AUTO_INTERVAL)

# ══ الأزرار ═════════════════════════════════════════════════════
def on_cb(cid,mid,cbid,data):
    answer(cbid)

    if data=="MAIN":
        edit(cid,mid,
            "⚡ <b>BASILISK TRADING BOT</b> • LIVE\n\n"
            f"🔄 Auto scan كل <code>{AUTO_INTERVAL//60}</code> دقائق\n"
            f"📊 <code>{len(SYMBOLS)}</code> أزواج | <code>{len(TIMEFRAMES)}</code> أطر زمنية\n"
            f"💵 Balance: <code>${BALANCE:,.2f}</code>",
            kb_main())

    elif data=="SCANALL":
        edit(cid,mid,"⚡ <b>SCANNING...</b>\n<i>انتظر دقيقتين</i>")
        found,_=do_scan(chat_id=cid)
        if not found:
            send(cid,
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ <b>BASILISK</b> • NO SIGNAL\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚪ لا توجد إشارات مؤكدة الآن\n"
                "<i>البوت يراقب تلقائياً</i>",
                kb_main())

    elif data=="BEST":
        edit(cid,mid,"🏆 <b>جاري البحث عن أقوى إشارة...</b>")
        found,best=do_scan(chat_id=cid)
        if not best:
            send(cid,"⚪ لا توجد إشارة قوية الآن",kb_main())

    elif data=="RISK":
        stake,profit,loss,bew=calc_risk(15)
        edit(cid,mid,
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>RISK CALCULATOR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Balance:  <code>${BALANCE:,.2f}</code>\n"
            f"⚠️ Risk:     <code>{RISK_PCT}%</code>\n"
            f"💲 Stake:    <code>${stake}</code>\n"
            f"✅ Profit:   <code>+${profit}</code>\n"
            f"❌ Loss:     <code>-${loss}</code>\n"
            f"📈 Break-even: <code>≥ {bew}%</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>لتغيير الرصيد أو نسبة المخاطرة\n"
            "غيّر BALANCE و RISK_PCT في Render</i>",
            kb_main())

    elif data=="SETTINGS":
        edit(cid,mid,
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ <b>SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ Auto Scan: كل <code>{AUTO_INTERVAL//60}</code> دقائق\n"
            f"🎯 Min Score: <code>{MIN_SCORE}</code>\n"
            f"🔕 Cooldown: <code>{COOLDOWN//60}</code> دقائق\n"
            f"📊 Pairs: <code>{len(SYMBOLS)}</code>\n"
            f"⏰ Auto TFs: <code>{', '.join(AUTO_TFS)}</code>\n"
            f"💵 Balance: <code>${BALANCE:,.2f}</code>\n"
            f"⚠️ Risk/Trade: <code>{RISK_PCT}%</code>",
            kb_main())

    elif data=="HELP":
        edit(cid,mid,
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "❓ <b>BASILISK BOT GUIDE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ <b>SCAN:</b> فحص كل الأزواج\n"
            "🏆 <b>BEST:</b> أقوى إشارة الآن\n"
            "📊 <b>زوج:</b> تحليل زوج محدد\n"
            "💰 <b>RISK:</b> حاسبة المخاطرة\n\n"
            "<b>المؤشرات (8):</b>\n"
            "• EMA 8/21/50/200\n"
            "• RSI + Stochastic\n"
            "• MACD Crossover\n"
            "• Bollinger Bands\n"
            "• ADX Trend Strength\n"
            "• Pivot S/R Levels\n"
            "• 12 Candle Patterns\n\n"
            "<b>SIGNAL STRENGTH:</b>\n"
            "WEAK → MEDIUM → STRONG\n"
            "⭐ → ⭐⭐⭐ → ⭐⭐⭐⭐⭐\n\n"
            "💡 تداول فقط على MEDIUM+ وما فوق",
            kb_main())

    elif data=="PAIRS":
        edit(cid,mid,"📊 <b>SELECT PAIR:</b>",kb_pairs())

    elif data.startswith("P:"):
        pair=data[2:]
        edit(cid,mid,f"⏱ <b>SELECT EXPIRY TIME for {otc(pair)}:</b>",kb_tf(pair))

    elif data.startswith("T:"):
        _,pair,tfk=data.split(":",2)
        tcfg=TIMEFRAMES.get(tfk)
        if not tcfg:
            send(cid,"❌ خطأ",kb_main()); return
        edit(cid,mid,f"⚡ <b>SCANNING {otc(pair)} | {tcfg['label']}...</b>")
        # ✅ do_scan مع pair و tfk — يرسل نتيجة هذا الزوج فقط
        do_scan(chat_id=cid, pair=pair, tfk=tfk)

# ══ الأوامر ═════════════════════════════════════════════════════
def on_cmd(cid,txt):
    if txt.strip() in ["/start","/menu"]:
        send(cid,
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ <b>BASILISK TRADING BOT v2.0</b>\n"
            "● LIVE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ 8 Technical Indicators\n"
            "✅ 12 Candlestick Patterns\n"
            "✅ Pivot S/R Levels\n"
            "✅ Risk Calculator\n"
            "✅ Signal Strength Meter\n"
            f"✅ {len(SYMBOLS)} OTC Pairs\n"
            "✅ Auto Scan كل 7 دقائق\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
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
    logging.info(f"⚡ BASILISK BOT v2.0 • port {port}")
    try:
        from waitress import serve
        serve(app,host="0.0.0.0",port=port)
    except ImportError:
        app.run(host="0.0.0.0",port=port)
