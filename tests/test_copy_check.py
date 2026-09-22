"""Copy-check arithmetic tests.

These use controlled price series rather than live data, because the thing
under test is the method: which day is the baseline, what counts as one copy
decision, and when we refuse to report a statistic at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

import copy_check  # noqa: E402
from copy_check import distinct_opportunities, measure, summarise  # noqa: E402

BENCHMARKS = {"stocks": "SPY", "crypto": "BTC"}
HORIZONS = [7, 30]

# 2026-01-02 is a Friday; the 3rd and 4th are the weekend.
ASSET = {
    "2026-01-01": 50.0,
    "2026-01-02": 100.0,
    "2026-01-05": 110.0,   # Monday - the first session after a Friday filing
    "2026-01-12": 121.0,   # +7d  -> +10% from the Monday baseline
    "2026-02-04": 132.0,   # +30d -> +20%
}
BENCH = {
    "2026-01-01": 10.0,
    "2026-01-02": 100.0,
    "2026-01-05": 100.0,
    "2026-01-12": 105.0,   # +5%
    "2026-02-04": 100.0,   # +0%
}


def _install_prices(monkeypatch_target=copy_check):
    def fake_series(symbol, start, end=None, *, crypto=False):
        return dict(BENCH) if symbol.upper() in ("SPY", "BTC") else dict(ASSET)
    monkeypatch_target.series_for = fake_series


def _trade(**kw):
    base = {
        "id": "t1", "person_id": "p1", "person": "Test Person",
        "ticker": "AAA", "action": "buy",
        "disclosure_date": "2026-01-02", "trade_date": "2025-12-20",
        "source": "house_clerk_ptr",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
def test_baseline_is_the_first_session_after_disclosure():
    _install_prices()
    result = measure(_trade(), HORIZONS, BENCHMARKS)

    assert result["measurable"] is True
    # Filed Friday the 2nd -> you could not act until Monday the 5th.
    # Using the 2nd would credit a price nobody outside could have got.
    assert result["baseline_date"] == "2026-01-05"
    assert result["baseline_price"] == 110.0


def test_returns_are_measured_against_the_benchmark():
    _install_prices()
    horizons = measure(_trade(), HORIZONS, BENCHMARKS)["horizons"]

    d7 = horizons["d7"]
    assert d7["status"] == "ok"
    assert d7["return_pct"] == 10.0          # 110 -> 121
    assert d7["benchmark_return_pct"] == 5.0  # 100 -> 105
    assert d7["excess_pct"] == 5.0

    d30 = horizons["d30"]
    assert d30["return_pct"] == 20.0
    assert d30["benchmark_return_pct"] == 0.0
    assert d30["excess_pct"] == 20.0


def test_missing_price_history_is_reported_not_guessed():
    copy_check.series_for = lambda *a, **k: {}
    result = measure(_trade(ticker="NOPE"), HORIZONS, BENCHMARKS)
    assert result["measurable"] is False
    assert "no free price history" in result["reason"]
    assert result["horizons"] == {}


def test_trade_without_ticker_is_unmeasurable():
    _install_prices()
    result = measure(_trade(ticker=None), HORIZONS, BENCHMARKS)
    assert result["measurable"] is False
    assert result["reason"] == "no ticker or disclosure date"


# ---------------------------------------------------------------------------
# One purchase spread over several accounts is ONE copy decision.
# ---------------------------------------------------------------------------
def test_same_ticker_same_day_counts_once():
    rows = [
        {"person_id": "p1", "ticker": "AVGO", "baseline_date": "2026-09-04"},
        {"person_id": "p1", "ticker": "AVGO", "baseline_date": "2026-09-04"},
        {"person_id": "p1", "ticker": "AVGO", "baseline_date": "2026-09-04"},
        {"person_id": "p1", "ticker": "AEP", "baseline_date": "2026-09-04"},
    ]
    collapsed = distinct_opportunities(rows)
    assert len(collapsed) == 2
    avgo = next(r for r in collapsed if r["ticker"] == "AVGO")
    assert avgo["underlying_filings"] == 3


def test_different_days_are_different_decisions():
    rows = [
        {"person_id": "p1", "ticker": "AVGO", "baseline_date": "2026-09-04"},
        {"person_id": "p1", "ticker": "AVGO", "baseline_date": "2026-10-04"},
    ]
    assert len(distinct_opportunities(rows)) == 2


# ---------------------------------------------------------------------------
# Refusing to publish a statistic is a feature.
# ---------------------------------------------------------------------------
def _measured(n: int, excess: float) -> list[dict]:
    return [
        {
            "measurable": True, "person_id": "p1", "person": "Test Person",
            "ticker": f"T{i}", "baseline_date": f"2026-01-{i + 1:02d}",
            "horizons": {
                "d7": {"status": "ok", "excess_pct": excess, "return_pct": excess},
                "today": {"status": "ok", "excess_pct": excess, "return_pct": excess},
            },
        }
        for i in range(n)
    ]


def test_below_threshold_reports_insufficient_data():
    summary = summarise(_measured(3, 5.0), [7], minimum=5)[0]
    block = summary["by_horizon"]["d7"]
    assert block["status"] == "insufficient_data"
    assert block["n"] == 3 and block["needed"] == 5
    assert "win_rate_vs_benchmark_pct" not in block


def test_at_threshold_reports_the_statistic():
    summary = summarise(_measured(5, 4.0), [7], minimum=5)[0]
    block = summary["by_horizon"]["d7"]
    assert block["status"] == "ok"
    assert block["n"] == 5
    assert block["win_rate_vs_benchmark_pct"] == 100.0
    assert block["avg_excess_pct"] == 4.0


def test_losses_are_reported_as_losses():
    summary = summarise(_measured(6, -3.5), [7], minimum=5)[0]
    block = summary["by_horizon"]["d7"]
    assert block["win_rate_vs_benchmark_pct"] == 0.0
    assert block["avg_excess_pct"] == -3.5


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
