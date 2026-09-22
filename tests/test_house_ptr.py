"""Parser tests built on real filings committed under tests/fixtures/.

Every expected value below was read by eye from the original PDF. If a test
fails, open the linked filing and check the PDF first - the filing is the
truth, not this file.

Run:  python -m pytest tests/ -v      (or: python tests/test_house_ptr.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from house_ptr import Filing, extract_text, parse_transactions  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _parse(fixture: str, **kw) -> list[dict]:
    defaults = dict(
        doc_id=kw.pop("doc_id", "0"),
        last=kw.pop("last", "Test"),
        first=kw.pop("first", "Filer"),
        district=kw.pop("district", "XX00"),
        filing_date=kw.pop("filing_date", "2026-01-01"),
        year=kw.pop("year", 2026),
    )
    filing = Filing(**defaults)
    text = extract_text((FIXTURES / fixture).read_bytes())
    return parse_transactions(text, filing)


# ---------------------------------------------------------------------------
# https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035480.pdf
# Pete Sessions (TX17), filed 09/18/2026. Two sales.
# ---------------------------------------------------------------------------
def test_sessions_two_sales():
    trades = _parse(
        "house_ptr_sessions_20035480.pdf",
        doc_id="20035480", last="Sessions", first="Pete", district="TX17",
        filing_date="2026-09-18",
    )
    assert len(trades) == 2

    abbv, googl = trades
    assert abbv["ticker"] == "ABBV"
    assert abbv["asset_name"] == "AbbVie Inc. Common Stock"
    assert abbv["action"] == "sell"
    assert abbv["trade_date"] == "2026-09-14"
    assert abbv["disclosure_date"] == "2026-09-18"
    assert abbv["delay_days"] == 4
    # The range must survive exactly as printed - never a midpoint.
    assert abbv["amount_label"] == "$15,001 - $50,000"
    assert abbv["amount_min"] == 15001
    assert abbv["amount_max"] == 50000
    assert abbv["asset_type"] == "ST"
    assert abbv["owner"] == "self"

    # Asset name wraps across two lines in the PDF; it must be rejoined.
    assert googl["ticker"] == "GOOGL"
    assert googl["asset_name"] == "Alphabet Inc. - Class A Common Stock"


# ---------------------------------------------------------------------------
# .../2026/20035196.pdf - Kevin Hern (OK01). Contains an Amended row and a
# Deleted row. A Deleted row was withdrawn by the filer and is NOT a trade.
# ---------------------------------------------------------------------------
def test_hern_amended_and_deleted():
    trades = _parse(
        "house_ptr_hern_amended_20035196.pdf",
        doc_id="20035196", last="Hern", first="Kevin", district="OK01",
    )
    by_ticker = {t["ticker"]: t for t in trades if t["ticker"]}

    assert by_ticker["OGN"]["filing_status"] == "Amended"
    assert by_ticker["OGN"]["withdrawn"] is False
    assert by_ticker["OGN"]["owner"] == "joint"
    # The Clerk's row id must be stripped out of the asset name.
    assert by_ticker["OGN"]["asset_name"] == "Organon & Co. Common Stock"
    assert by_ticker["OGN"]["tx_id"] == "2000166537"

    assert by_ticker["VSNT"]["filing_status"] == "Deleted"
    assert by_ticker["VSNT"]["withdrawn"] is True


def test_no_asset_name_starts_with_a_row_id():
    trades = _parse("house_ptr_hern_amended_20035196.pdf", doc_id="20035196")
    for t in trades:
        assert not (t["asset_name"] or "").strip()[:1].isdigit(), t["asset_name"]


# ---------------------------------------------------------------------------
# .../2026/20035244.pdf - Kelly Morrison (MN03). Spouse-owned rows plus a
# filer note explaining a spinoff.
# ---------------------------------------------------------------------------
def test_morrison_spouse_and_notes():
    trades = _parse(
        "house_ptr_morrison_notes_20035244.pdf",
        doc_id="20035244", last="Morrison", first="Kelly", district="MN03",
    )
    assert trades, "expected at least one parsed trade"
    assert any(t["owner"] == "spouse" for t in trades)

    spinoff = [t for t in trades if t.get("filer_note") and "spinoff" in t["filer_note"]]
    assert spinoff, "filer note about the SPGI spinoff should be captured"
    assert spinoff[0]["ticker"] == "MBGL"


# ---------------------------------------------------------------------------
# A scanned paper filing. It must produce NO trades - never a guess.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# .../2026/20035186.pdf - Josh Gottheimer (NJ05). Three rows carry a security
# name long enough to wrap over five lines. The FIRST of those lines holds both
# the start of the name and the owner code, so a parser that keeps too few
# lines silently reports a joint holding as the member's own.
# ---------------------------------------------------------------------------
def test_long_security_names_keep_their_first_line():
    trades = _parse(
        "house_ptr_gottheimer_longname_20035186.pdf",
        doc_id="20035186", last="Gottheimer", first="Josh", district="NJ05",
        filing_date="2026-08-06",
    )
    # 13 transaction rows in the document. Two of them - GTLS and FN - put the
    # asset name and the transaction on one line, and were silently dropped
    # while the parser anchored the row pattern to the start of the line.
    assert len(trades) == 13

    alphabet = [t for t in trades if (t["ticker"] or "").startswith("GOOG")]
    assert len(alphabet) == 3
    for trade in alphabet:
        assert trade["asset_name"].startswith("Alphabet Inc. - Depositary Shares"), (
            f"name was truncated to: {trade['asset_name'][:60]}"
        )
        assert "Mandatory Convertible Preferred Stock" in trade["asset_name"]
        # The owner code rides on the dropped first line.
        assert trade["owner"] == "joint", "JT prefix was lost with the first line"

    # Every row in this filing is jointly held.
    assert all(t["owner"] == "joint" for t in trades)


def test_inline_rows_are_not_dropped():
    """A row whose asset name shares a line with its transaction tail.

    The filing contains:
        JT Chart Industries, Inc. (GTLS) [ST] S 07/17/2026 08/06/2026 $1,001 - $15,000
    Matching only at the start of a line missed 541 of these across 146 filings.
    """
    trades = _parse(
        "house_ptr_gottheimer_longname_20035186.pdf",
        doc_id="20035186", last="Gottheimer", first="Josh", district="NJ05",
        filing_date="2026-08-06",
    )
    by_ticker = {t["ticker"]: t for t in trades if t["ticker"]}

    gtls = by_ticker.get("GTLS")
    assert gtls, f"inline row dropped; found {sorted(by_ticker)}"
    assert gtls["asset_name"] == "Chart Industries, Inc"
    assert gtls["action"] == "sell"
    assert gtls["trade_date"] == "2026-07-17"
    assert gtls["owner"] == "joint"       # the JT prefix is on the same line
    assert gtls["amount_label"] == "$1,001 - $15,000"

    fn = by_ticker.get("FN")
    assert fn, "second inline row dropped"
    assert fn["asset_name"] == "Fabrinet Ordinary Shares"
    assert fn["action"] == "sell"
    assert fn["trade_date"] == "2026-07-14"


def test_scanned_filing_yields_nothing():
    text = extract_text((FIXTURES / "house_ptr_scanned_9116331.pdf").read_bytes())
    assert len(text.strip()) < 200, "fixture is supposed to be an image-only scan"
    filing = Filing("9116331", "Harshbarger", "Diana", "TN01", "2026-09-11", 2026)
    assert parse_transactions(text, filing) == []


# ---------------------------------------------------------------------------
# Invariants that must hold for every parsed row, on every filing.
# ---------------------------------------------------------------------------
def test_universal_invariants():
    for fixture in (
        "house_ptr_sessions_20035480.pdf",
        "house_ptr_hern_amended_20035196.pdf",
        "house_ptr_morrison_notes_20035244.pdf",
    ):
        for t in _parse(fixture):
            assert t["action"] in ("buy", "sell", "exchange"), t
            assert t["trade_date"] and t["trade_date"][:2] == "20", t
            assert t["amount_min"] is not None, t
            # A disclosure can never precede the trade it discloses.
            if t["delay_days"] is not None:
                assert t["delay_days"] >= 0, t
            # Amount label must be literal text from the filing.
            assert "$" in t["amount_label"], t
            if t["amount_max"] is not None:
                assert t["amount_max"] >= t["amount_min"], t


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
