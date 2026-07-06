#!/usr/bin/env python3
import os
import time
import requests
import threading
import logging
import io
from datetime import datetime

import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
from flask import Flask

# ====================== إعدادات ======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk")
CHAT_ID   = int(os.environ.get("CHAT_ID", "8674500253"))
BALANCE   = float(os.environ.get("BALANCE", "1000"))
RISK_PCT  = float(os.environ.get("RISK_PCT", "2.0"))

SYMBOLS = {
    "XAU/USD": "GC=F",
    "EUR/USD": "EURUSD=X",
    "AUD/USD": "AUDUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "EUR/GBP": "EURGBP=X",
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def send_message(cid, text, photo=None):
    try:
        if photo:
            files = {'photo': ('chart.png', photo, 'image/png')}
            data = {'chat_id': cid, 'caption': text, 'parse_mode': 'HTML'}
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", 
                         data=data, files=files, timeout=30)
        else:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                         json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=30)
    except:
        pass

# رسم الشموع
def plot_candles(df, pair, sig):
    fig, ax = plt.subplots(figsize=(12, 7))
    up = df[df.close >= df.open]
    down = df[df.close < df.open]
    
    ax.bar(up.index, up.close - up.open, bottom=up.open, color='green', width=0.6)
    ax.bar(down.index, down.close - down.open, bottom=down.open, color='red', width=0.6)
    
    ax.plot(df.index, df['close'].rolling(8).mean(), color='blue', label='EMA8')
    ax.plot(df.index, df['close'].rolling(21).mean(), color='orange', label='EMA21')
    
    plt.title(f"{pair} - {sig}")
    plt.legend()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=200, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    return buf

# التحليل (تم تصليح منطق البيع)
def analyze(df):
    if len(df) < 50:
        return None, {}
    
    cl = df["close"].astype(float)
    p = float(cl.iloc[-1])
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1] or 0.0001

    e8 = cl.ewm(span=8).mean().iloc[-1]
    e21 = cl.ewm(span=21).mean().iloc[-1]
    
    # RSI
    delta = cl.diff()
    gain = delta.clip(lower=0).ewm(span=14).mean()
    loss = -delta.clip(upper=0).ewm(span=14).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    score = 0
    reasons = []

    # شروط الشراء
    if e8 > e21: 
        score += 5
        reasons.append("EMA صاعد")
    if rsi.iloc[-1] < 30: 
        score += 4
        reasons.append("RSI Oversold")
    if p < cl.rolling(20).min().iloc[-1] * 1.005: 
        score += 3
        reasons.append("دعم قوي")

    # شروط البيع
    if e8 < e21: 
        score -= 5
        reasons.append("EMA هابط")
    if rsi.iloc[-1] > 70: 
        score -= 4
        reasons.append("RSI Overbought")
    if p > cl.rolling(20).max().iloc[-1] * 0.995: 
        score -= 3
        reasons.append("مقاومة قوية")

    if score >= 8:
        return "🟢 BUY", {
            "price": round(p, 5), 
            "score": score, 
            "why": " | ".join(reasons), 
            "stake": round(BALANCE * RISK_PCT / 100, 2)
        }
    elif score <= -8:
        return "🔴 SELL", {
            "price": round(p, 5), 
            "score": score, 
            "why": " | ".join(reasons), 
            "stake": round(BALANCE * RISK_PCT / 100, 2)
        }
    return None, {}

# الماسح اليدوي
def manual_scan(chat_id):
    send_message(chat_id, "🔍 **جاري فحص جميع الأزواج...**")
    for name, ticker in SYMBOLS.items():
        time.sleep(1.8)
        try:
            df = yf.download(ticker, interval="5m", period="10d", progress=False)
            df.columns = [c.lower() for c in df.columns]
            
            sig, info = analyze(df)
            if sig:
                photo = plot_candles(df.tail(120), name, sig)
                msg = f"""⚡ **BASILISK SCANNER**
**{name}**

{sig}
السعر: {info['price']}
Stake: ${info['stake']}
السبب: {info['why']}
🕒 {datetime.now().strftime('%H:%M:%S')}"""
                send_message(chat_id, msg, photo=photo.getvalue())
        except Exception as e:
            print(f"Error scanning {name}: {e}")
            continue
    send_message(chat_id, "✅ **انتهى الفحص**")

# ====================== Telegram Bot ======================
def telegram_bot():
    last_update = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update+1}&timeout=10",
                timeout=15
            ).json()
            
            for update in res.get("result", []):
                last_update = update["update_id"]
                if "message" in update:
                    msg = update["message"]
                    cid = msg["chat"]["id"]
                    text = msg.get("text", "").strip()
                    
                    if text in ["/scan", "/start"]:
                        threading.Thread(target=manual_scan, args=(cid,), daemon=True).start()
                    elif text == "/help":
                        send_message(cid, """🛠 **أوامر البوت:**
/scan → فحص جميع الأزواج
/start → نفس الأمر
/help → هذه الرسالة""")
        except:
            time.sleep(5)

# ====================== Flask ======================
@app.route("/")
def home():
    return "✅ BASILISK Scanner جاهز للعمل"

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    print("🚀 BASILISK Scanner Started...")
    threading.Thread(target=telegram_bot, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8080))
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False)
