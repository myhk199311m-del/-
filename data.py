"""
data.py
Fetches OHLC candles from Twelve Data (https://twelvedata.com).

IMPORTANT NOTE ON "OTC" PAIRS:
Broker OTC pairs (e.g. the synthetic weekend/overnight pairs shown by
Pocket Option, Quotex, etc.) are generated internally by each broker and
are NOT published through any public market-data API. There is no
legitimate external source for that exact feed. This bot uses the real
underlying market symbol (e.g. AUD/USD) as the closest available proxy.
Label pairs as "OTC" in the UI only if you understand this limitation.
"""

import os
import requests
import pandas as pd

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
BASE_URL = "https://api.twelvedata.com/time_series"


class DataError(Exception):
    pass


def fetch_candles(symbol: str, interval: str, output_size: int = 100) -> pd.DataFrame:
    """
    symbol: e.g. "AUD/USD", "XAU/USD", "BTC/USD"
    interval: "1min", "5min", "15min"
    """
    if not TWELVE_DATA_API_KEY:
        raise DataError("TWELVE_DATA_API_KEY environment variable is not set.")

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": output_size,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
    }
    resp = requests.get(BASE_URL, params=params, timeout=15)
    payload = resp.json()

    if payload.get("status") == "error":
        raise DataError(payload.get("message", "Unknown Twelve Data error"))

    values = payload.get("values")
    if not values:
        raise DataError("No candle data returned.")

    df = pd.DataFrame(values)
    df = df.rename(columns={"datetime": "time"})
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    # Twelve Data returns newest-first; reverse to chronological order
    df = df.iloc[::-1].reset_index(drop=True)
    return df[["time", "open", "high", "low", "close"]]
