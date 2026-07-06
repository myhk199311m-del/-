import os
import time
import threading
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE = float(os.environ.get("BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "2"))
PAYOUT = 85
MAIN_TF = "1د"

SYMBOLS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CAD": "USDCAD=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "EUR/GBP": "EURGBP=X",
}

TIMEFRAMES = {
    "1د": {"interval": "1m", "period": "1d", "exp_min": 1},
    "5د": {"interval": "5m", "period": "5d", "exp_min": 5},
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

def tg(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    return requests.post(url, json=data, timeout=20).json()

def send(cid, txt, kb=None):
    p = {"chat_id": cid, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb:
        p["reply_markup"] = kb
    return tg("sendMessage", p)

def edit(cid, mid, txt, kb=None):
    p = {"chat_id": cid, "message_id": mid, "text": txt, "parse_mode": "HTML"}
    if kb:
        p["reply_markup"] = kb
    return tg("editMessageText", p)

def answer(cbid):
    tg("answerCallbackQuery", {"callback_query_id": cbid})

def kb_main():
    return {"inline_keyboard": [
        [{"text": "📊 PAIRS", "callback_data": "PAIRS"}],
        [{"text": "RESET", "callback_data": "RESET"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows = [[{"text": f"{p} OTC", "callback_data": f"P:{p}"}] for p in names]
    rows.append([{"text": "◄ BACK", "callback_data": "MAIN"}])
    return {"inline_keyboard": rows}

def fetch(ticker, interval, period, min_bars=50):
    try:
        df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        df = df[["open", "high", "low", "close"]].dropna()
        if len(df) < min_bars:
            return pd.DataFrame()
        return df.copy()
    except Exception:
        return pd.DataFrame()

def EMA(s, p):
    return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100/(1 + g/(l + 1e-9))

def analyze(df):
    if df is None or df.empty or len(df) < 50:
        return "NO SIGNAL", {"reason": "بيانات غير كافية"}

    cl = df["close"]
    e9 = EMA(cl, 9)
    e21 = EMA(cl, 21)
    rsi = float(RSI(cl, 14).iloc[-1])

    buy = 0
    sell = 0
    why_buy = []
    why_sell = []

    if e9.iloc[-2] < e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
        buy += 3
        why_buy.append("EMA Bullish")

    if e9.iloc[-2] > e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
        sell += 3
        why_sell.append("EMA Bearish")

    if rsi < 35:
        buy += 2
        why_buy.append(f"RSI {rsi:.0f}")

    if rsi > 65:
        sell += 2
        why_sell.append(f"RSI {rsi:.0f}")

    score = max(buy, sell)
    if score < 4:
        return "WAIT", {"reason": "إشارة ضعيفة", "rsi": rsi}

    sig = "BUY" if buy > sell else "SELL"
    why = " | ".join(why_buy if sig == "BUY" else why_sell)

    stake = round(BALANCE * (RISK_PCT / 100), 2)
    profit = round(stake * (PAYOUT / 100), 2)

    det = {
        "price": round(float(cl.iloc[-1]), 5),
        "rsi": round(rsi, 1),
        "conf": min(99, int((score / 6) * 100)),
        "stake": stake,
        "profit": profit,
        "why": why,
    }
    return sig, det

def fmt(pair, sig, det):
    if sig == "NO SIGNAL":
        return f"━━━━━━━━━━━━━━━━━━━━
<b>NO SIGNAL</b>
{det['reason']}
━━━━━━━━━━━━━━━━━━━━"
    if sig == "WAIT":
        return f"━━━━━━━━━━━━━━━━━━━━
<b>WAIT</b>
{det['reason']}
RSI: {det.get('rsi', 0):.0f}
━━━━━━━━━━━━━━━━━━━━"

    arrow = "▲" if sig == "BUY" else "▼"
    circle = "🟢" if sig == "BUY" else "🔴"
    return (
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"⚡ <b>BASILISK SIGNAL</b>
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"📌 PAIR: {pair} OTC
"
        f"⏱ TIME: {TIMEFRAMES[MAIN_TF]['exp_min']}m
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"{arrow} {circle} <b>{sig}</b> {arrow}
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"🎯 Confidence: {det['conf']}%
"
        f"💵 Balance: ${BALANCE:,.2f}
"
        f"⚠️ Risk: ${det['stake']:.1f}
"
        f"✅ Profit: +${det['profit']:.1f}
"
        f"📈 RSI: {det['rsi']}
"
        f"☑️ {det['why']}
"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def do_scan(cid, pair):
    sent = send(cid, f"🔍 Scanning {pair} OTC...")
    mid = sent.get("result", {}).get("message_id")
    df = fetch(SYMBOLS[pair], TIMEFRAMES[MAIN_TF]["interval"], TIMEFRAMES[MAIN_TF]["period"])
    sig, det = analyze(df)
    msg = fmt(pair, sig, det)
    if mid:
        edit(cid, mid, msg, kb_main())
    else:
        send(cid, msg, kb_main())

def on_cmd(cid, txt):
    if txt in ["/start", "/menu"]:
        send(cid, "⚡ <b>BASILISK OTC</b>

اختر زوج للتحليل:", kb_main())

def on_cb(cid, mid, cbid, data):
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "⚡ <b>BASILISK OTC</b>

اختر زوج للتحليل:", kb_main())
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر الزوج:", kb_pairs())
    elif data == "RESET":
        edit(cid, mid, "✅ Reset Done", kb_main())
    elif data.startswith("P:"):
        pair = data[2:]
        threading.Thread(target=do_scan, args=(cid, pair), daemon=True).start()

def polling():
    last = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": last + 1, "timeout": 30, "allowed_updates": json.dumps(["message", "callback_query"])},
                timeout=35
            ).json()
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

@app.route("/")
def home():
    return "✅ BASILISK OTC v1"

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ضيف BOT_TOKEN")
    else:
        threading.Thread(target=polling, daemon=True).start()
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
