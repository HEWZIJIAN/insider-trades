"""OGE Form 278-T parser tests.

The test that matters most here is test_damaged_ocr_is_rejected. The
President's own filings are scans whose OCR layer renders "Purchase" as
"ourchose" and "$500,001" as "$500 001". A parser that tries its best on that
input produces rows that look real and are not. This suite exists to keep the
parser refusing them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from exec_278t import AMOUNT_BANDS, extract_text, parse_rows  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

ZINBERG = {
    "url": "https://www.whitehouse.gov/wp-content/uploads/2026/02/"
           "Zinberg-Joel-Periodic-Transaction-Report-09.03.25.pdf",
    "label": "Zinberg, Joel - Periodic Transaction Report 09.03.25",
    "person": "Zinberg, Joel",
    "disclosure_date": "2025-09-03",
    "amended": False,
}

TRUMP = {
    "url": "https://www.whitehouse.gov/wp-content/uploads/2026/01/"
           "President-Donald-J.-Trump-Periodic-Transaction-Report-1.14.2026-.pdf",
    "label": "President Donald J. Trump Periodic Transaction Report 01.14.26",
    "person": "President Donald J. Trump",
    "disclosure_date": "2026-01-14",
    "amended": False,
}


# ---------------------------------------------------------------------------
# A clean, born-digital staff filing. Six transactions, read off the PDF.
# ---------------------------------------------------------------------------
def test_clean_filing_parses_exactly():
    text = extract_text((FIXTURES / "oge278t_zinberg.pdf").read_bytes())
    rows, rejected = parse_rows(text, ZINBERG)

    assert rejected == 0
    assert len(rows) == 6

    assert rows[0]["asset_name"] == "North Hempstead, N.Y. GO Bond"
    assert rows[0]["action"] == "buy"
    assert rows[0]["trade_date"] == "2025-08-29"
    assert rows[0]["amount_label"] == "$1,001 - $15,000"
    assert rows[0]["amount_min"] == 1001
    assert rows[0]["amount_max"] == 15000

    # Row 4's description wraps across two lines and carries a ticker.
    wrapped = rows[3]
    assert wrapped["asset_name"] == (
        "Vanguard Dividend Appreciation Index Fund ETF Class Shares (VIG)"
    )
    assert wrapped["ticker"] == "VIG"

    assert [r["action"] for r in rows] == ["buy"] * 4 + ["sell"] * 2


# ---------------------------------------------------------------------------
# The whole point. 36,000 characters of damaged OCR must yield ZERO rows.
# ---------------------------------------------------------------------------
def test_damaged_ocr_is_rejected():
    text = (FIXTURES / "oge278t_trump_damaged_ocr.txt").read_text(encoding="utf-8")
    assert len(text) > 30_000, "fixture should be the full damaged filing"

    rows, rejected = parse_rows(text, TRUMP)

    assert rows == [], (
        f"damaged OCR must never produce trades; got {len(rows)}: "
        f"{[r['asset_name'] for r in rows[:3]]}"
    )
    assert rejected >= 3, "transaction-shaped lines should be counted as rejected"


def test_damaged_ocr_would_be_linked_not_published():
    """Mirrors the decision process() makes from parse_rows' output."""
    text = (FIXTURES / "oge278t_trump_damaged_ocr.txt").read_text(encoding="utf-8")
    rows, rejected = parse_rows(text, TRUMP)
    linked_only = bool(rejected) and rejected >= max(3, len(rows))
    assert linked_only, "this filing must fall back to a linked document"


# ---------------------------------------------------------------------------
# Vocabulary guards.
# ---------------------------------------------------------------------------
def test_amount_must_be_an_exact_oge_band():
    # A comma lost to OCR ("$500 001") must not be accepted as a real band.
    assert "$500 001 - $1 000 000" not in AMOUNT_BANDS
    assert "$500,001 - $1,000,000" in AMOUNT_BANDS

    text = extract_text((FIXTURES / "oge278t_zinberg.pdf").read_bytes())
    rows, _ = parse_rows(text, ZINBERG)
    for row in rows:
        assert row["amount_label"] in AMOUNT_BANDS, row["amount_label"]


def test_ocr_mangled_type_words_are_not_accepted():
    """The real manglings of the word "Purchase" seen in Trump's filings."""
    mangled = ["ourchose", "lourchaso", "ourd,.oso", "PUrchaso", "1ourchose", "purdlnso"]
    for word in mangled:
        line = f"1 Some Municipal Bond {word} 12/04/2025 No $1,001 - $15,000"
        rows, _ = parse_rows(line, TRUMP)
        assert rows == [], f"'{word}' must not be read as a transaction type"

    # The correctly spelled word on the same line shape does parse.
    good = "1 Some Municipal Bond Purchase 12/04/2025 No $1,001 - $15,000"
    rows, _ = parse_rows(good, TRUMP)
    assert len(rows) == 1 and rows[0]["action"] == "buy"


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
