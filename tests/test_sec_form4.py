"""Form 4 parser tests, built on real filings committed under tests/fixtures/.

The point these tests defend: a Form 4 row is not automatically a trade.
Grants (A) and shares withheld for tax (F) are not something anyone could copy,
and treating them as buys/sells would make the copy-check meaningless.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from sec_form4 import TX_CODES, parse_form4  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _parse(fixture: str, accession: str, filing_date: str, form: str = "4"):
    filing = {
        "form": form,
        "accession": accession,
        "filing_date": filing_date,
        "report_date": filing_date,
        "primary_document": "primary_doc.xml",
        "issuer_name": "Trump Media & Technology Group Corp.",
        "issuer_tickers": ["DJT"],
        "cik": "0001849635",
    }
    return parse_form4((FIXTURES / fixture).read_bytes(), filing)


# ---------------------------------------------------------------------------
# Accession 0001437749-26-028743, filed 2026-08-21.
# Kevin McGurn, Interim CEO. Code F - shares withheld to cover tax.
# This is the case that must NOT look like a sale.
# ---------------------------------------------------------------------------
def test_tax_withholding_is_not_a_sale():
    rows = _parse("sec_form4_djt_mcgurn.xml", "0001437749-26-028743", "2026-08-21")
    assert len(rows) == 1
    row = rows[0]

    assert row["transaction_code"] == "F"
    assert row["action"] == "tax"
    assert row["copyable"] is False, "tax withholding must never count as a trade"
    assert row["person"] == "McGurn Kevin"
    assert "Interim CEO" in row["role"]
    assert row["shares"] == 7958
    assert row["price_per_share"] == 8.8864
    assert row["trade_date"] == "2026-08-21"
    # Form 4's headline feature: the resulting position.
    assert row["shares_owned_after"] == 112853
    assert row["ownership"] == "D"
    assert row["footnotes"], "the withholding footnote should be captured"


# ---------------------------------------------------------------------------
# Accession 0002015663-25-000003. A genuine open-market purchase (code P).
# ---------------------------------------------------------------------------
def test_open_market_purchase_is_copyable():
    rows = _parse("sec_form4_djt_glabe_purchase.xml", "0002015663-25-000003", "2025-11-19")
    buys = [r for r in rows if r["transaction_code"] == "P"]
    assert buys, "fixture should contain an open-market purchase"

    buy = buys[0]
    assert buy["action"] == "buy"
    assert buy["copyable"] is True
    assert buy["shares"] == 1000
    assert buy["price_per_share"] == 10.465
    assert buy["value_usd"] == 10465.0
    assert buy["shares_owned_after"] == 326236
    assert buy["ticker"] == "DJT"


# ---------------------------------------------------------------------------
# Accession 0001474506-25-000169. The filer typed the wrong YEAR in the
# transaction date: it says 2024-09-11 while periodOfReport and the filing date
# both say 2025-09-11. We must publish the date as filed AND flag it.
# ---------------------------------------------------------------------------
def test_contradictory_date_is_flagged_not_corrected():
    rows = _parse("sec_form4_djt_glabe_datetypo.xml", "0001474506-25-000169", "2025-09-11")
    row = next(r for r in rows if r["transaction_code"] == "S")

    # Reported exactly as filed - we do not silently rewrite the source.
    assert row["trade_date"] == "2024-09-11"
    assert row["period_of_report"] == "2025-09-11"
    # ...but the contradiction is surfaced rather than passed off as real.
    assert row["date_anomaly"], "a 365-day Form 4 lag must be flagged"
    assert "2 business days" in row["date_anomaly"]


# ---------------------------------------------------------------------------
# Code table invariants.
# ---------------------------------------------------------------------------
def test_only_open_market_codes_are_copyable():
    copyable = {code for code, (_, _, ok) in TX_CODES.items() if ok}
    assert copyable == {"P", "S"}, (
        "only open-market purchases and sales can be copied; "
        f"got {sorted(copyable)}"
    )


def test_every_row_has_required_fields():
    for fixture, acc, date in (
        ("sec_form4_djt_mcgurn.xml", "0001437749-26-028743", "2026-08-21"),
        ("sec_form4_djt_glabe_purchase.xml", "0002015663-25-000003", "2025-11-19"),
        ("sec_form4_djt_glabe_datetypo.xml", "0001474506-25-000169", "2025-09-11"),
    ):
        for row in _parse(fixture, acc, date):
            assert row["person"], row
            assert row["trade_date"], row
            assert row["transaction_code"], row
            assert isinstance(row["copyable"], bool), row
            assert row["source_url"].startswith("https://www.sec.gov/"), row
            # A value may only exist when both inputs were actually reported.
            if row["value_usd"] is not None:
                assert row["shares"] and row["price_per_share"], row


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
