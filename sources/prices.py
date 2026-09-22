"""Free daily price history for the copy-check.

Stocks/ETFs: Yahoo Finance's chart endpoint. No key, no registration.
Crypto:      CoinGecko's public API. No key.

Both are free and unmetered for the handful of calls we make, but neither is a
contract - Yahoo's endpoint in particular is undocumented. Every failure is
recorded so the app can say "price data unavailable" instead of inventing a
number. Stooq was evaluated and dropped: it now sits behind a proof-of-work
bot challenge.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time

from common import cache_path, get, now_iso, update_status, write_json

SOURCE_ID = "prices"
YAHOO = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={start}&period2={end}&interval=1d"
)
COINGECKO = (
    "https://api.coingecko.com/api/v3/coins/{coin}/market_chart/range"
    "?vs_currency=usd&from={start}&to={end}"
)

# Cache price series for a day; history does not change retroactively.
CACHE_SECONDS = 24 * 60 * 60

# Each ticker is fetched ONCE, over a wide window, and sliced in memory.
#
# Keying the cache by (ticker, start date) instead meant AAPL was fetched again
# for every distinct disclosure date - 1,274 requests for 572 tickers. Repeated
# every 30 minutes in CI that would be abusive to a free, undocumented service.
HISTORY_FLOOR = "2019-01-01"
# CoinGecko's free tier only serves 365 days of range data.
CRYPTO_MAX_DAYS = 360

# Within a single run, never touch the disk twice for the same series.
_MEMO: dict[str, dict[str, float]] = {}

COIN_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "USDT": "tether", "USDC": "usd-coin", "WLFI": "world-liberty-financial",
    "TRUMP": "official-trump",
}


def _epoch(date: str | dt.date) -> int:
    if isinstance(date, str):
        date = dt.date.fromisoformat(date)
    return int(dt.datetime.combine(date, dt.time()).replace(tzinfo=dt.timezone.utc).timestamp())


def _load_cached(key: str) -> dict | None:
    path = cache_path("prices", f"{key}.json")
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_SECONDS:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _store(key: str, series: dict) -> None:
    cache_path("prices", f"{key}.json").write_text(
        json.dumps(series), encoding="utf-8"
    )


def daily_closes(symbol: str, start: str = HISTORY_FLOOR, end: str | None = None) -> dict[str, float]:
    """{'YYYY-MM-DD': close} for a stock or ETF. Empty dict if unavailable.

    The whole history is fetched and cached once per ticker; `start` and `end`
    only slice what comes back.
    """
    symbol = symbol.upper()
    key = f"eq-{symbol}"
    end = end or dt.date.today().isoformat()

    series = _MEMO.get(key)
    if series is None:
        series = _load_cached(key)
    if series is None:
        try:
            resp = get(
                YAHOO.format(
                    symbol=symbol,
                    start=_epoch(HISTORY_FLOOR),
                    end=_epoch(dt.date.today().isoformat()),
                ),
                browser_ua=True,
            )
            result = resp.json()["chart"]["result"][0]
            stamps = result.get("timestamp") or []
            closes = result["indicators"]["quote"][0].get("close") or []
        except Exception:
            _MEMO[key] = {}
            return {}

        series = {
            dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat(): float(px)
            for ts, px in zip(stamps, closes)
            if px is not None
        }
        if series:
            _store(key, series)
    _MEMO[key] = series

    return {d: p for d, p in series.items() if start <= d <= end}


def daily_closes_crypto(symbol: str, start: str = "", end: str | None = None) -> dict[str, float]:
    """{'YYYY-MM-DD': close} for a crypto asset. Empty dict if unavailable.

    Cached once per coin, like equities. CoinGecko's free tier caps range
    queries at a year, so that is the window we keep.
    """
    symbol = symbol.upper()
    coin = COIN_IDS.get(symbol)
    if not coin:
        return {}

    key = f"cx-{symbol}"
    today = dt.date.today()
    end = end or today.isoformat()
    start = start or (today - dt.timedelta(days=CRYPTO_MAX_DAYS)).isoformat()

    series = _MEMO.get(key)
    if series is None:
        series = _load_cached(key)
    if series is None:
        window_start = (today - dt.timedelta(days=CRYPTO_MAX_DAYS)).isoformat()
        try:
            resp = get(COINGECKO.format(
                coin=coin, start=_epoch(window_start), end=_epoch(today.isoformat()),
            ))
            points = resp.json().get("prices") or []
        except Exception:
            _MEMO[key] = {}
            return {}

        series = {}
        for ms, px in points:
            day = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()
            series[day] = float(px)  # last point of each day wins
        if series:
            _store(key, series)
    _MEMO[key] = series

    return {d: p for d, p in series.items() if start <= d <= end}


def series_for(symbol: str, start: str, end: str | None = None, *, crypto: bool = False):
    return (daily_closes_crypto if crypto else daily_closes)(symbol, start, end)


def first_close_on_or_after(series: dict[str, float], date: str) -> tuple[str, float] | None:
    """The first available trading day on or after `date`, with its close."""
    for day in sorted(series):
        if day >= date:
            return day, series[day]
    return None


def close_on_or_before(series: dict[str, float], date: str) -> tuple[str, float] | None:
    for day in sorted(series, reverse=True):
        if day <= date:
            return day, series[day]
    return None


def health_check() -> dict:
    """Prove both providers respond, and record it for the UI."""
    today = dt.date.today()
    start = (today - dt.timedelta(days=10)).isoformat()

    spy = daily_closes("SPY", start)
    btc = daily_closes_crypto("BTC", start)

    report = {
        "checked_at": now_iso(),
        "equities": {
            "provider": "Yahoo Finance chart API",
            "ok": bool(spy),
            "points": len(spy),
            "latest": max(spy) if spy else None,
            "latest_close": spy[max(spy)] if spy else None,
        },
        "crypto": {
            "provider": "CoinGecko public API",
            "ok": bool(btc),
            "points": len(btc),
            "latest": max(btc) if btc else None,
            "latest_close": btc[max(btc)] if btc else None,
        },
    }
    write_json("price_health.json", report)
    ok = report["equities"]["ok"] and report["crypto"]["ok"]
    update_status(
        SOURCE_ID,
        ok=ok,
        detail=f"equities={report['equities']['ok']} crypto={report['crypto']['ok']}",
    )
    return report


if __name__ == "__main__":
    rep = health_check()
    for market in ("equities", "crypto"):
        block = rep[market]
        state = "OK" if block["ok"] else "UNAVAILABLE"
        print(
            f"{market:<9} {state:<12} {block['provider']:<28} "
            f"{block['points']} days, latest {block['latest']} = {block['latest_close']}"
        )
    sys.exit(0 if rep["equities"]["ok"] and rep["crypto"]["ok"] else 1)
