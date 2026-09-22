"""Copy-check: what would have happened if you had mirrored a disclosed buy?

Method, stated plainly because the method IS the result:

  Baseline   The close on the first trading day STRICTLY AFTER the disclosure
             date. That is the earliest moment an outsider reading the filing
             could have acted. Using the trade date instead would measure the
             insider's entry, which nobody outside could have got.
  Horizons   +7, +30, +90 calendar days from that baseline, plus "to today".
             Each uses the last close on or before the horizon date.
  Benchmark  SPY for stocks, BTC for crypto, over the identical window.
  Result     excess = asset return - benchmark return.

Only disclosed BUYS are measured, per the brief. Sells are shown in the app but
not scored: "would copying this sale have made money" has no answer without
knowing what you held.

Anything that cannot be measured honestly is reported as unmeasurable with a
reason, never as a zero.

This is a backtest of public filings. It is not investment advice.
"""
from __future__ import annotations

import datetime as dt
import statistics
import sys

from common import delay_days, load_watchlist, now_iso, read_json, update_status, write_json
from prices import close_on_or_before, first_close_on_or_after, series_for

SOURCE_ID = "copy_check"
CRYPTO_SOURCES = {"crypto"}

# A baseline must be close to the disclosure. Anything further away is not the
# trade we set out to measure. Ten days covers a long weekend plus a holiday.
MAX_BASELINE_GAP_DAYS = 10


def _is_crypto(trade: dict) -> bool:
    return trade.get("source") in CRYPTO_SOURCES or trade.get("asset_class") == "crypto"


def _pct(new: float, old: float) -> float | None:
    if not old:
        return None
    return round((new / old - 1.0) * 100, 2)


def measure(trade: dict, horizons: list[int], benchmarks: dict) -> dict:
    """Score one disclosed buy. Never raises; returns a reason when it can't."""
    ticker = (trade.get("ticker") or "").upper()
    disclosed = trade.get("disclosure_date")
    crypto = _is_crypto(trade)
    result = {
        "trade_id": trade.get("id"),
        "person_id": trade.get("person_id"),
        "person": trade.get("person"),
        "ticker": ticker,
        "asset_class": "crypto" if crypto else "equity",
        "disclosure_date": disclosed,
        "trade_date": trade.get("trade_date"),
        "benchmark": benchmarks["crypto"] if crypto else benchmarks["stocks"],
        "measurable": False,
        "reason": None,
        "baseline_date": None,
        "baseline_price": None,
        "horizons": {},
    }
    if not ticker or not disclosed:
        result["reason"] = "no ticker or disclosure date"
        return result

    today = dt.date.today()
    start = (dt.date.fromisoformat(disclosed) - dt.timedelta(days=5)).isoformat()

    asset = series_for(ticker, start, crypto=crypto)
    if not asset:
        result["reason"] = f"no free price history available for {ticker}"
        return result

    bench_symbol = result["benchmark"]
    bench = series_for(bench_symbol, start, crypto=crypto)
    if not bench:
        result["reason"] = f"benchmark {bench_symbol} unavailable"
        return result

    # Strictly after disclosure: the filing is public, you act the next session.
    day_after = (dt.date.fromisoformat(disclosed) + dt.timedelta(days=1)).isoformat()
    base = first_close_on_or_after(asset, day_after)
    bench_base = first_close_on_or_after(bench, day_after)
    if not base or not bench_base:
        result["reason"] = "no trading day priced after the disclosure date"
        return result

    baseline_date, baseline_price = base
    _, bench_baseline = bench_base

    # If the first price we can find is long after the disclosure, we are not
    # measuring "what if you had copied this" - we are measuring a different
    # trade entirely. Thinly traded OTC tickers do this: the filing is from
    # June 2025 but the first available close is a year later. Refuse it.
    gap = delay_days(disclosed, baseline_date)
    if gap is not None and gap > MAX_BASELINE_GAP_DAYS:
        result["reason"] = (
            f"no price within {MAX_BASELINE_GAP_DAYS} days of disclosure "
            f"(first available close was {baseline_date}, {gap} days later)"
        )
        result["measurable"] = False
        return result
    result.update(
        measurable=True, baseline_date=baseline_date, baseline_price=round(baseline_price, 4)
    )

    def at(target: str) -> dict:
        asset_point = close_on_or_before(asset, target)
        bench_point = close_on_or_before(bench, target)
        if not asset_point or not bench_point or asset_point[0] < baseline_date:
            return {"status": "pending", "detail": "horizon not reached yet"}
        asset_ret = _pct(asset_point[1], baseline_price)
        bench_ret = _pct(bench_point[1], bench_baseline)
        if asset_ret is None or bench_ret is None:
            return {"status": "unavailable", "detail": "price missing"}
        return {
            "status": "ok",
            "as_of": asset_point[0],
            "price": round(asset_point[1], 4),
            "return_pct": asset_ret,
            "benchmark_return_pct": bench_ret,
            "excess_pct": round(asset_ret - bench_ret, 2),
        }

    for days in horizons:
        target = (dt.date.fromisoformat(baseline_date) + dt.timedelta(days=days)).isoformat()
        result["horizons"][f"d{days}"] = (
            {"status": "pending", "detail": f"+{days}d falls in the future"}
            if target > today.isoformat()
            else at(target)
        )
    result["horizons"]["today"] = at(today.isoformat())
    return result


def distinct_opportunities(rows: list[dict]) -> list[dict]:
    """Collapse rows that represent the SAME copy decision.

    Filers routinely report one purchase several times because it happened
    across several accounts - an IRA, a joint brokerage and a 401(k) on the
    same day. Those are separate transactions and the feed shows them all, but
    to a copier they are one decision at one price. Counting them individually
    would inflate n and weight that ticker several times in the win rate.
    """
    seen: dict[tuple, dict] = {}
    for row in rows:
        key = (row["person_id"], row["ticker"], row["baseline_date"])
        entry = seen.get(key)
        if entry is None:
            row = dict(row, underlying_filings=1)
            seen[key] = row
        else:
            entry["underlying_filings"] += 1
    return list(seen.values())


def summarise(measured: list[dict], horizons: list[int], minimum: int) -> list[dict]:
    """Per-person win rate and average excess, with an explicit n."""
    by_person: dict[str, list[dict]] = {}
    for row in measured:
        if row["measurable"]:
            by_person.setdefault(row["person_id"], []).append(row)
    by_person = {pid: distinct_opportunities(rows) for pid, rows in by_person.items()}

    out = []
    for pid, rows in by_person.items():
        entry = {
            "person_id": pid,
            "person": rows[0]["person"],
            # Distinct copy decisions, not raw filing rows - see
            # distinct_opportunities() for why these differ.
            "measured_buys": len(rows),
            "underlying_filing_rows": sum(r.get("underlying_filings", 1) for r in rows),
            # Thirty buys disclosed on one day is one decision about one day's
            # market, not thirty independent calls. Surface that rather than
            # letting a single filing masquerade as a track record.
            "distinct_baseline_dates": len({r["baseline_date"] for r in rows}),
            "by_horizon": {},
        }
        for key in [f"d{d}" for d in horizons] + ["today"]:
            excesses = [
                r["horizons"][key]["excess_pct"]
                for r in rows
                if r["horizons"].get(key, {}).get("status") == "ok"
            ]
            returns = [
                r["horizons"][key]["return_pct"]
                for r in rows
                if r["horizons"].get(key, {}).get("status") == "ok"
            ]
            if len(excesses) < minimum:
                entry["by_horizon"][key] = {
                    "status": "insufficient_data",
                    "n": len(excesses),
                    "needed": minimum,
                }
                continue
            entry["by_horizon"][key] = {
                "status": "ok",
                "n": len(excesses),
                "win_rate_vs_benchmark_pct": round(
                    100 * sum(1 for e in excesses if e > 0) / len(excesses), 1
                ),
                "avg_return_pct": round(statistics.fmean(returns), 2),
                "avg_excess_pct": round(statistics.fmean(excesses), 2),
                "median_excess_pct": round(statistics.median(excesses), 2),
            }
        out.append(entry)

    out.sort(key=lambda e: -e["measured_buys"])
    return out


def run() -> dict:
    cfg = load_watchlist()
    horizons = cfg.get("copy_check_horizons", [7, 30, 90])
    minimum = int(cfg.get("min_trades_for_stats", 5))
    benchmarks = cfg.get("benchmarks", {"stocks": "SPY", "crypto": "BTC"})

    trades = read_json("trades.json", default=[]) or []
    buys = [t for t in trades if t.get("copy_eligible") and t.get("action") == "buy"]

    measured = [measure(t, horizons, benchmarks) for t in buys]
    people = summarise(measured, horizons, minimum)

    ok_count = sum(1 for m in measured if m["measurable"])
    payload = {
        "generated_at": now_iso(),
        "method": {
            "baseline": "close on the first trading day strictly after the disclosure date",
            "horizons_days": horizons,
            "benchmarks": benchmarks,
            "measures": "disclosed BUYS only; sells are shown but not scored",
            "deduplication": (
                "the same ticker bought by one person on one baseline date counts "
                "once, however many accounts it was filed across"
            ),
            "min_trades_for_stats": minimum,
            "disclaimer": (
                "A backtest of public filings, not investment advice. Amounts are "
                "disclosed as ranges, so position sizing cannot be reproduced."
            ),
        },
        "counts": {
            "eligible_buys": len(buys),
            "measured": ok_count,
            "unmeasurable": len(measured) - ok_count,
        },
        "people": people,
        "trades": measured,
    }
    write_json("copy_check.json", payload)
    update_status(
        SOURCE_ID,
        ok=True,
        detail=f"{ok_count}/{len(buys)} eligible buys measured",
        count=ok_count,
    )
    return payload


if __name__ == "__main__":
    out = run()
    counts = out["counts"]
    print(f"eligible buys : {counts['eligible_buys']}")
    print(f"measured      : {counts['measured']}")
    print(f"unmeasurable  : {counts['unmeasurable']}")
    sys.exit(0)
