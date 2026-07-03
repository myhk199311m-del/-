# Basilisk Signal Bot (Telegram)

Recreates the dashboard from the screenshot: Pair / Payout / Time panel,
manual **Scan**, confidence-score bar, pattern name, strength badge, and a
Risk Calculator (balance, risk %, payout %, stake, P/L, break-even win rate).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your tokens
export $(cat .env | xargs)   # or use your platform's env var settings
python bot.py
```

Deploys the same way as your existing bots (Railway/Render): set
`TELEGRAM_BOT_TOKEN` and `TWELVE_DATA_API_KEY` as environment variables in
the platform dashboard, point the start command at `python bot.py`.

## Important note on "OTC" pairs

Broker OTC pairs (the synthetic weekend/overnight feeds shown on
Pocket Option, Quotex, etc.) are generated internally by each broker.
No public API — Twelve Data included — publishes that exact feed. This bot
pulls the real underlying market symbol (e.g. `AUD/USD`) as the closest
available proxy. If your broker's OTC price action diverges from the real
market, signals will diverge too. Swap `data.py` for your broker's own
feed if you have access to one.

## Files

- `bot.py` — Telegram handlers, inline-keyboard dashboard
- `indicators.py` — 9-indicator engine (EMA trend, EMA cross, RSI, MACD,
  ADX/DI, Bollinger, Stochastic, Williams %R, CCI) + candlestick pattern
  detection (Pin Bar / Engulfing / Doji) + confidence scoring
- `risk.py` — stake / profit / loss / break-even calculator
- `data.py` — Twelve Data candle fetcher

## Customizing pairs / timeframes

Edit `PAIRS` and `TIMEFRAMES` at the top of `bot.py`.
