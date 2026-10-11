"""Market quotes: Yahoo Finance chart API (via httpx), yfinance fallback, manual entry otherwise."""
from __future__ import annotations

import logging

import httpx

from .db import Database, now_iso

log = logging.getLogger("finance.quotes")


def fetch_quote(ticker: str) -> tuple[float, str] | None:
    try:
        r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                      params={"range": "1d", "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            meta = r.json()["chart"]["result"][0]["meta"]
            return float(meta["regularMarketPrice"]), meta.get("currency") or "USD"
    except Exception as e:  # noqa: BLE001
        log.info("yahoo chart failed for %s: %s", ticker, e)
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        return float(fi["last_price"]), fi.get("currency") or "USD"
    except Exception as e:  # noqa: BLE001
        log.info("yfinance failed for %s: %s", ticker, e)
    return None


def refresh_prices(db: Database, tickers: list[str]) -> dict:
    ok, failed = {}, []
    for t in sorted(set(x.upper() for x in tickers if x)):
        q = fetch_quote(t)
        if q:
            ok[t] = q[0]
            set_price(db, t, q[0], q[1], "yahoo")
        else:
            failed.append(t)
    return {"updated": ok, "failed": failed}


def set_price(db: Database, ticker: str, price: float, currency: str = "USD", source: str = "manual"):
    with db.tx() as con:
        con.execute("""INSERT INTO prices(ticker,price,currency,source,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET price=excluded.price, currency=excluded.currency,
            source=excluded.source, updated_at=excluded.updated_at""", (ticker.upper(), price, currency, source, now_iso()))
