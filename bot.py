import os
import time
import json
import logging
import threading
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, Dict, Any

import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN", "8792351652:AAEMzaulBCrCjQotcCdVlGdcJQSNUPcCiAk").strip()
CHAT_ID = int(os.getenv("CHAT_ID", "8674500253") or 0)
BALANCE = float(os.getenv("BALANCE", "1000"))
RISK_PCT = float(os.getenv("RISK_PCT", "2"))
PAYOUT = int(os.getenv("PAYOUT", "85"))

MAIN_TF = os.getenv("MAIN_TF", "1m")
MIN_SCORE = int(os.getenv("MIN_SCORE", "4"))
MIN_ADX = float(os.getenv("MIN_ADX", "15"))
ZIGZAG_DEPTH = int(os.getenv("ZIGZAG_DEPTH", "8"))
FETCH_DELAY = float(os.getenv("FETCH_DELAY", "1.5"))
SIGNAL_FILE = os.getenv("SIGNAL_FILE", "basilisk_signals.json")
LOG_FILE = os.getenv("LOG_FILE", "basilisk_log.csv")

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
    "1m": {"interval": "1m", "period": "1d", "bars": 150, "exp_min": 1},
    "5m": {"interval": "5m", "period": "5d", "bars": 150, "exp_min": 5},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("basilisk")

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ BASILISK OTC v5.1 • MANUAL MODE"

def tg(method: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN missing"}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data,
            timeout=20
        )
        return r.json()
    except Exception as e:
        logger.exception("Telegram error: %s", e)
        return {"ok": False, "description": str(e)}

def send(cid: int, txt: str, kb: Optional[dict] = None) -> Optional[int]:
    payload = {
        "chat_id": cid,
        "text": txt,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if kb:
        payload["reply_markup"] = kb
    res = tg("sendMessage", payload)
    return res.get("result", {}).get("message_id") if res.get("ok") else None

def edit(cid: int, mid: int, txt: str, kb: Optional[dict] = None) -> bool:
    payload = {
        "chat_id": cid,
        "message_id": mid,
        "text": txt,
        "parse_mode": "HTML",
    }
    if kb:
        payload["reply_markup"] = kb
    res = tg("editMessageText", payload)
    return bool(res.get("ok"))

def answer(cbid: str) -> None:
    tg("answerCallbackQuery", {"callback_query_id": cbid})

def kb_main():
    return {"inline_keyboard": [
        [{"text": "📊 PAIRS", "callback_data": "PAIRS"}],
        [{"text": "RESET", "callback_data": "RESET"}],
    ]}

def kb_pairs():
    names = list(SYMBOLS.keys())
    rows = [
        [{"text": f"{p} OTC", "callback_data": f"P:{p}"} for p in names[i:i+2]]
        for i in range(0, len(names), 2)
    ]
    rows.append([{"text": "◄ BACK", "callback_data": "MAIN"}])
    return {"inline_keyboard": rows}

def fetch(ticker: str, interval: str, period: str, min_bars: int = 150) -> pd.DataFrame:
    for attempt in range(3):
        try:
            if attempt:
                time.sleep(5 * attempt)
            df = yf.download(
                ticker,
                interval=interval,
                period=period,
                progress=False,
                auto_adjust=False,
                threads=False
            )
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            need = {"open", "high", "low", "close"}
            if not need.issubset(df.columns):
                continue
            df = df[list(need)].dropna()
            if len(df) >= min_bars:
                return df.copy()
        except Exception as e:
            logger.warning("fetch failed %s attempt=%s err=%s", ticker, attempt + 1, e)
    return pd.DataFrame()

def EMA(s, p):
    return s.ewm(span=p, adjust=False).mean()

def RSI(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100 / (1 + g / (l + 1e-9))

def ADX(h, l, c, p=14):
    up = h.diff()
    down = -l.diff()
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.rolling(p).mean().replace(0, 1e-9)
    pdi = 100 * pd.Series(pdm, index=h.index).rolling(p).mean() / atr_
    ndi = 100 * pd.Series(ndm, index=h.index).rolling(p).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
    return dx.rolling(p).mean()

def ATR(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def BB(s, p=20, std=2):
    ma = s.rolling(p).mean()
    stdv = s.rolling(p).std()
    return ma + stdv * std, ma, ma - stdv * std

def ZIGZAG(df, depth=8):
    hi, lo = df["high"], df["low"]
    pts = []
    for i in range(depth, len(df) - depth):
        window_h = hi.iloc[i-depth:i+depth+1]
        window_l = lo.iloc[i-depth:i+depth+1]
        if hi.iloc[i] == window_h.max():
            pts.append((i, float(hi.iloc[i]), "H"))
        if lo.iloc[i] == window_l.min():
            pts.append((i, float(lo.iloc[i]), "L"))
    pts.sort(key=lambda x: x[0])
    if len(pts) < 4:
        return pts, "NONE"
    vals = [x[1] for x in pts[-5:]]
    if len(vals) >= 5:
        if vals[0] > vals[2] and vals[1] < vals[3] and vals[4] < vals[3]:
            return pts, "M-TOP"
        if vals[0] < vals[2] and vals[1] > vals[3] and vals[4] > vals[3]:
            return pts, "W-BOTTOM"
    return pts, "NONE"

def detect_candle_pattern(df):
    if len(df) < 4:
        return "Pin Bar"
    o, h, l, c = df["open"].iloc[-2], df["high"].iloc[-2], df["low"].iloc[-2], df["close"].iloc[-2]
    o2, c2 = df["open"].iloc[-3], df["close"].iloc[-3]
    if c > o and o < c2 and c > o2 and c2 < o2:
        return "Engulfing ↑"
    if c < o and o > c2 and c < o2 and c2 > o2:
        return "Engulfing ↓"
    o3, c3 = df["open"].iloc[-4], df["close"].iloc[-4]
    rng = h - l + 1e-9
    if c3 > o3 and abs(c2 - o2) / rng < 0.3 and c < o and c < (o3 + c3) / 2:
        return "Evening Star ↓"
    if c3 < o3 and abs(c2 - o2) / rng < 0.3 and c > o and c > (o3 + c3) / 2:
        return "Morning Star ↑"
    return "Pin Bar"

@dataclass
class Signal:
    pair: str
    side: str
    score: int
    confidence: int
    power: int
    reason: str
    price: float
    rsi: float
    adx: float
    atr: float
    bb_u: float
    bb_l: float
    pattern: str
    candle: str
    stake: float
    profit: float
    payout: int
    break_even: float
    strength: str
    stars: str
    exp: int

def analyze_basilisk(df: pd.DataFrame) -> Tuple[str, int, Dict[str, Any]]:
    if df is None or df.empty or len(df) < 60:
        return "NO SIGNAL", 0, {"reason": "بيانات غير كافية"}

    cl, hi, lo = df["close"], df["high"], df["low"]
    e9, e21 = EMA(cl, 9), EMA(cl, 21)
    rsi_val = float(RSI(cl, 7).iloc[-1])
    adx_val = float(ADX(hi, lo, cl, 14).iloc[-1])
    atr_val = float(ATR(hi, lo, cl, 14).iloc[-1])
    bb_u, bb_m, bb_l = BB(cl, 20, 2)
    _, pattern = ZIGZAG(df, ZIGZAG_DEPTH)
    candle_pat = detect_candle_pattern(df)

    if np.isnan(adx_val) or np.isnan(rsi_val) or np.isnan(atr_val):
        return "WAIT", 0, {"reason": "مؤشرات غير مكتملة", "adx": adx_val, "rsi": rsi_val, "atr": atr_val}

    if adx_val < MIN_ADX:
        return "WAIT", 0, {"reason": "السوق ضعيف", "adx": adx_val, "rsi": rsi_val, "atr": atr_val}

    buy = 0
    sell = 0
    buy_reasons = []
    sell_reasons = []

    if pattern == "W-BOTTOM":
        buy += 4
        buy_reasons.append("W-BOTTOM")
    elif pattern == "M-TOP":
        sell += 4
        sell_reasons.append("M-TOP")

    if e9.iloc[-2] < e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
        buy += 3
        buy_reasons.append("EMA Bullish")
    if e9.iloc[-2] > e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
        sell += 3
        sell_reasons.append("EMA Bearish")

    if rsi_val < 35:
        buy += 2
        buy_reasons.append(f"RSI {rsi_val:.0f}")
    if rsi_val > 65:
        sell += 2
        sell_reasons.append(f"RSI {rsi_val:.0f}")

    if "↑" in candle_pat:
        buy += 2
        buy_reasons.append(candle_pat)
    if "↓" in candle_pat:
        sell += 2
        sell_reasons.append(candle_pat)

    if buy == sell:
        return "WAIT", 0, {"reason": "إشارة متعادلة", "adx": adx_val, "rsi": rsi_val, "atr": atr_val}

    side = "BUY" if buy > sell else "SELL"
    score = max(buy, sell)
    if score < MIN_SCORE:
        return "WAIT", 0, {"reason": "إشارة ضعيفة", "adx": adx_val, "rsi": rsi_val, "atr": atr_val}

    confidence = min(99, int(score / 12 * 100))
    stake = round(BALANCE * (RISK_PCT / 100), 2)
    profit = round(stake * (PAYOUT / 100), 2)
    break_even = round(100 / (1 + PAYOUT / 100), 1)

    reasons = buy_reasons if side == "BUY" else sell_reasons
    det = {
        "price": round(float(cl.iloc[-1]), 5),
        "rsi": round(rsi_val, 1),
        "adx": round(adx_val, 1),
        "atr": round(atr_val, 5),
        "bb_u": round(float(bb_u.iloc[-1]), 5),
        "bb_l": round(float(bb_l.iloc[-1]), 5),
        "pattern": pattern,
        "candle": candle_pat,
        "conf": confidence,
        "power": score,
        "stake": stake,
        "profit": profit,
        "payout": PAYOUT,
        "be": break_even,
        "strength": "WEAK+" if confidence < 70 else "STRONG",
        "stars": "⭐⭐" if confidence < 70 else "⭐⭐⭐",
        "exp": TIMEFRAMES[MAIN_TF]["exp_min"],
        "why": " | ".join(reasons) if reasons else "No confluence",
    }
    return side, score, det

def fmt_basilisk(pair: str, sig: str, det: Dict[str, Any]) -> str:
    if sig == "NO SIGNAL":
        return "━━━━━━━━━━━━━━━━━━━━
<b>NO SIGNAL</b>
بيانات غير كافية
━━━━━━━━━━━━━━━━━━━━"
    if sig == "WAIT":
        return (
            f"━━━━━━━━━━━━━━━━━━━━
"
            f"<b>BASILISK</b>
"
            f"{pair} OTC | {TIMEFRAMES[MAIN_TF]['exp_min']}m

"
            f"<b>WAIT</b>
"
            f"{det.get('reason', '')}
"
            f"ADX: {det.get('adx', 0):.0f}
"
            f"RSI: {det.get('rsi', 0):.0f}
"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    arrow = "▲" if sig == "BUY" else "▼"
    circle = "🟢" if sig == "BUY" else "🔴"
    confidence_bar = "█" * max(1, det["conf"] // 10)
    confidence_bar = confidence_bar.ljust(10, "░")

    return (
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"⚡ <b>BASILISK SIGNAL • LIVE</b>
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"📌 PAIR: {pair} OTC
"
        f"💰 PAYOUT: {det['payout']}%
"
        f"⏱ TIME: 0{TIMEFRAMES[MAIN_TF]['exp_min']}:00
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"{arrow} {circle} <b>{sig}</b> {arrow}
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"📊 Power: {det['power']} / 12
"
        f"🎯 Confidence: {det['conf']}% [{confidence_bar}]
"
        f"🏆 {det['strength']} {det['stars']}
"
        f"🕯️ Pattern: {det['candle']}
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"💰 <b>RISK CALCULATOR</b>
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"💵 Balance: ${BALANCE:,.2f}
"
        f"⚠️ Risk {RISK_PCT}%: ${det['stake']:.1f}
"
        f"✅ Potential Profit: +${det['profit']:.1f}
"
        f"❌ Potential Loss: -${det['stake']:.1f}
"
        f"📈 Break-even: ≥ {det['be']}%
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"📉 RSI: {det['rsi']} | ADX: {det['adx']} | ATR: {det['atr']:.5f}
"
        f"📊 BB: {det['bb_u']:.5f} | {det['bb_l']:.5f}
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"☑️ {det['why']}
"
        f"━━━━━━━━━━━━━━━━━━━━
"
        f"⚠️ Not financial advice"
    )

def log_signal(pair: str, sig: str, det: Dict[str, Any]) -> None:
    row = {
        "ts": datetime.utcnow().isoformat(),
        "pair": pair,
        "signal": sig,
        **det
    }
    file_exists = os.path.exists(LOG_FILE)
    pd.DataFrame([row]).to_csv(LOG_FILE, mode="a", index=False, header=not file_exists)

def save_last_signal(pair: str, sig: str, det: Dict[str, Any]) -> None:
    data = {"pair": pair, "signal": sig, "details": det, "ts": datetime.utcnow().isoformat()}
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def do_scan(chat_id: int, pair: str, status_mid: Optional[int] = None) -> None:
    try:
        if status_mid is None:
            status_mid = send(chat_id, f"🔍 Scanning {pair} OTC | {MAIN_TF}...")
        time.sleep(FETCH_DELAY)
        df = fetch(SYMBOLS[pair], TIMEFRAMES[MAIN_TF]["interval"], TIMEFRAMES[MAIN_TF]["period"])
        sig, score, det = analyze_basilisk(df)
        if sig in {"BUY", "SELL"}:
            log_signal(pair, sig, det)
            save_last_signal(pair, sig, det)
        msg = fmt_basilisk(pair, sig, det)
        if status_mid:
            if not edit(chat_id, status_mid, msg, kb_main()):
                send(chat_id, msg, kb_main())
        else:
            send(chat_id, msg, kb_main())
    except Exception as e:
        logger.exception("do_scan failed: %s", e)
        send(chat_id, f"❌ Error while scanning {pair}: {e}", kb_main())

def on_cmd(cid: int, txt: str) -> None:
    if txt in ["/start", "/menu"]:
        send(cid, "⚡ <b>BASILISK OTC v5.1</b>

اختر زوج للتحليل:", kb_main())

def on_cb(cid: int, mid: int, cbid: str, data: str) -> None:
    answer(cbid)
    if data == "MAIN":
        edit(cid, mid, "⚡ <b>BASILISK OTC v5.1</b>

اختر زوج للتحليل:", kb_main())
    elif data == "PAIRS":
        edit(cid, mid, "📊 اختر الزوج:", kb_pairs())
    elif data == "RESET":
        edit(cid, mid, "✅ Reset Done", kb_main())
    elif data.startswith("P:"):
        pair = data[2:]
        threading.Thread(target=do_scan, args=(cid, pair, mid), daemon=True).start()

def polling():
    last = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": last + 1, "timeout": 50},
                timeout=60
            ).json()
            for upd in res.get("result", []):
                last = upd["update_id"]
                if "message" in upd:
                    msg = upd["message"]
                    on_cmd(msg["chat"]["id"], msg.get("text", ""))
                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    on_cb(
                        cb["message"]["chat"]["id"],
                        cb["message"]["message_id"],
                        cb["id"],
                        cb["data"]
                    )
        except Exception as e:
            logger.error("polling error: %s", e)
            time.sleep(3)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ضيف BOT_TOKEN")
    else:
        threading.Thread(target=polling, daemon=True).start()
        port = int(os.getenv("PORT", "8080"))
        app.run(host="0.0.0.0", port=port)
