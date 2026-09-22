"""Pick N random parsed trades and show each next to its original filing.

For every sampled trade this prints what we stored, then the raw text actually
extracted from the source document around that transaction, then the link.
If the two disagree, the filing is right and the parser is wrong.

Usage:  python scripts/audit_sample.py [N] [--seed 42]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from common import DATA, cache_path  # noqa: E402


def _extract(source: str, record: dict) -> str:
    """Re-extract the original document text for this record."""
    try:
        if source == "house_clerk_ptr":
            import house_ptr
            path = cache_path("house", f"2026-{record['doc_id']}.pdf")
            if not path.exists():
                return "(source PDF not in local cache)"
            return house_ptr.extract_text(path.read_bytes())
        if source == "oge_278t":
            import exec_278t
            name = exec_278t.slug(record["source_url"].rsplit("/", 1)[-1]) + ".pdf"
            path = cache_path("oge", name)
            if not path.exists():
                return "(source PDF not in local cache)"
            return exec_278t.extract_text(path.read_bytes())
        if source == "sec_form4":
            path = cache_path("sec", f"{record['doc_id']}.xml")
            if not path.exists():
                return "(source XML not in local cache)"
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(could not re-read source: {exc})"
    return "(unknown source)"


def _form4_fields(xml_text: str, record: dict) -> str:
    """Read the matching transaction straight out of the Form 4 XML."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return f"   (could not parse XML: {exc})"

    def value(node, path):
        el = node.find(path)
        if el is None:
            return None
        child = el.find("value")
        target = child if child is not None else el
        return (target.text or "").strip() if target.text else None

    lines = [f"      {'reporting owner':<32} {value(root, './/reportingOwnerId/rptOwnerName')}",
             f"      {'issuer':<32} {value(root, './/issuerName')}"
             f"  ({value(root, './/issuerTradingSymbol')})"]

    want_shares = record.get("shares")
    transactions = root.findall(".//nonDerivativeTransaction") + \
        root.findall(".//derivativeTransaction")
    for tx in transactions:
        shares = value(tx, "transactionAmounts/transactionShares")
        try:
            same = want_shares is not None and abs(float(shares) - float(want_shares)) < 0.5
        except (TypeError, ValueError):
            same = False
        if not same and len(transactions) > 1:
            continue
        lines += [
            f"      {'security':<32} {value(tx, 'securityTitle')}",
            f"      {'transactionDate':<32} {value(tx, 'transactionDate')}",
            f"      {'transactionCode':<32} "
            f"{value(tx, 'transactionCoding/transactionCode')}",
            f"      {'shares':<32} {shares}",
            f"      {'pricePerShare':<32} "
            f"{value(tx, 'transactionAmounts/transactionPricePerShare')}",
            f"      {'sharesOwnedFollowing':<32} "
            f"{value(tx, 'postTransactionAmounts/sharesOwnedFollowingTransaction')}",
        ]
        break
    return "\n".join(lines)


def _window(text: str, record: dict, radius: int = 3) -> str:
    """Lines of the original document around this transaction."""
    lines = text.splitlines()

    # Anchor on the asset first. A filing routinely contains several rows with
    # the same date, so searching by date alone lands on the wrong transaction
    # and makes a correct parse look wrong.
    anchors: list[str] = []
    if record.get("ticker"):
        anchors.append(f"({record['ticker']})")
    if record.get("asset_name"):
        words = record["asset_name"].split()
        anchors.append(" ".join(words[:4]))
        anchors.append(" ".join(words[:2]))

    date_needle = None
    if record.get("trade_date"):
        y, m, d = record["trade_date"].split("-")
        date_needle = f"{m}/{d}/{y}"

    best = None
    for anchor in anchors:
        hits = [i for i, line in enumerate(lines) if anchor and anchor in line]
        if not hits:
            continue
        # Prefer the occurrence whose transaction row carries the right date.
        if date_needle and len(hits) > 1:
            for hit in hits:
                nearby = " ".join(lines[hit: hit + 4])
                if date_needle in nearby:
                    best = hit
                    break
        if best is None:
            best = hits[0]
        break

    if best is None and date_needle:
        best = next((i for i, line in enumerate(lines) if date_needle in line), None)
    if best is None:
        return "(could not locate this row in the source text)"

    lo, hi = max(0, best - radius), min(len(lines), best + radius + 1)
    out = []
    for i in range(lo, hi):
        marker = ">>" if i == best else "  "
        out.append(f"   {marker} {lines[i].strip()[:150]}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    trades = json.loads((DATA / "trades.json").read_text(encoding="utf-8"))
    parsed = [t for t in trades if t.get("parsed")]
    if not parsed:
        print("No parsed trades found. Run the fetchers first.")
        return 1

    rng = random.Random(args.seed)
    sample = rng.sample(parsed, min(args.n, len(parsed)))

    print("=" * 96)
    print(f"ACCURACY AUDIT - {len(sample)} randomly sampled trades vs their original filings")
    if args.seed is not None:
        print(f"(seed {args.seed} - rerun with the same seed to get the same sample)")
    print("=" * 96)

    for i, record in enumerate(sample, 1):
        print(f"\n[{i}] {record.get('person')}  -  {record.get('source_label')}")
        print("-" * 96)
        print("  WE STORED:")
        print(f"     asset      : {record.get('asset_name')}")
        print(f"     ticker     : {record.get('ticker') or '(none reported)'}")
        print(f"     action     : {record.get('action')}", end="")
        if record.get("transaction_code"):
            print(f"   (Form 4 code {record['transaction_code']}"
                  f" = {record.get('transaction_code_label')})", end="")
        print()
        if record.get("amount_label"):
            print(f"     amount     : {record['amount_label']}   <- as printed in the filing")
        if record.get("shares") is not None:
            price = record.get("price_per_share")
            print(f"     shares     : {record['shares']:,.0f}"
                  + (f" @ ${price}" if price else ""))
        if record.get("shares_owned_after") is not None:
            print(f"     owned after: {record['shares_owned_after']:,.0f}")
        print(f"     trade date : {record.get('trade_date')}")
        print(f"     disclosed  : {record.get('disclosure_date')}  "
              f"(delay {record.get('delay_days')} days)")
        if record.get("owner") and record["owner"] != "self":
            print(f"     owner      : {record['owner']}")
        if record.get("subholding"):
            print(f"     account    : {record['subholding']}")
        if record.get("filing_status"):
            print(f"     status     : {record['filing_status']}")
        if record.get("date_anomaly"):
            print(f"     FLAGGED    : {record['date_anomaly']}")
        if not record.get("copy_eligible"):
            print(f"     excluded from copy-check: "
                  f"{'; '.join(record.get('copy_ineligible_reasons') or [])}")

        print("\n  ORIGINAL FILING SAYS:")
        text = _extract(record.get("source"), record)
        if record.get("source") == "sec_form4":
            print(_form4_fields(text, record))
        else:
            print(_window(text, record))

        print(f"\n  VERIFY AT: {record.get('source_url')}")

    print("\n" + "=" * 96)
    print("Open any link above and compare. If a stored value differs from the filing,")
    print("tell me the number and I will fix the parser and add a test for it.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
