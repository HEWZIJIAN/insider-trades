"""SEC EDGAR - Form 4 (and Form 144) for Trump-linked public companies.

Form 4 is the richest source we have: it is XML, filed within two business
days, and reports the exact share count and price, plus "shares owned
following transaction", which gives a real current position.

The trap: most Form 4 rows are NOT trades. A grant (code A) or shares withheld
to cover tax (code F) is not something you could copy. Only open-market
purchases (P) and sales (S) are treated as copyable; everything else is kept,
labelled, and excluded from the copy-check.

SEC requires a User-Agent with a contact address and caps clients at 10
requests/second. See sources/common.py.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter

from common import (
    SEC_LIMITER,
    cache_path,
    delay_days,
    get,
    load_watchlist,
    parse_date,
    slug,
    update_status,
    write_json,
)

SOURCE_ID = "sec_form4"
SOURCE_LABEL = "SEC EDGAR - Form 4"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"
FILING_PAGE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"

# Table I/II transaction codes, from the Form 4 instructions.
# "copyable" marks the only two an outsider could actually have mirrored.
TX_CODES = {
    "P": ("buy", "Open-market or private purchase", True),
    "S": ("sell", "Open-market or private sale", True),
    "A": ("grant", "Grant, award or other acquisition from the issuer", False),
    "F": ("tax", "Shares withheld to pay exercise price or tax", False),
    "M": ("exercise", "Exercise or conversion of a derivative security", False),
    "C": ("conversion", "Conversion of a derivative security", False),
    "X": ("exercise", "Exercise of an in- or at-the-money derivative", False),
    "G": ("gift", "Bona fide gift", False),
    "D": ("disposition", "Disposition to the issuer", False),
    "V": ("other", "Transaction voluntarily reported earlier", False),
    "J": ("other", "Other acquisition or disposition", False),
    "K": ("other", "Equity swap or similar", False),
    "U": ("other", "Disposition in a tender of shares", False),
    "W": ("other", "Acquisition or disposition by will or laws of descent", False),
    "I": ("other", "Discretionary transaction", False),
}


def _text(node, path: str) -> str | None:
    if node is None:
        return None
    el = node.find(path)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _float(node, path: str) -> float | None:
    raw = _text(node, path)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def list_filings(cik: str, forms=("4", "4/A", "144")) -> list[dict]:
    """Return recent filings of the given forms for one issuer CIK."""
    cik_padded = str(cik).zfill(10)
    resp = get(SUBMISSIONS.format(cik=cik_padded), limiter=SEC_LIMITER)
    data = resp.json()
    recent = data["filings"]["recent"]

    out = []
    for form, acc, filed, doc, report in zip(
        recent["form"],
        recent["accessionNumber"],
        recent["filingDate"],
        recent["primaryDocument"],
        recent.get("reportDate", [""] * len(recent["form"])),
    ):
        if form not in forms:
            continue
        out.append(
            {
                "form": form,
                "accession": acc,
                "filing_date": filed,
                "report_date": report,
                "primary_document": doc,
                "issuer_name": data.get("name"),
                "issuer_tickers": data.get("tickers", []),
                "cik": cik_padded,
            }
        )
    return out


def _xml_url(filing: dict) -> str:
    """EDGAR lists the styled view (xslF345X0N/foo.xml); we want raw foo.xml."""
    doc = filing["primary_document"]
    if "/" in doc:
        doc = doc.split("/")[-1]
    return ARCHIVE.format(
        cik_int=int(filing["cik"]),
        acc_nodash=filing["accession"].replace("-", ""),
        doc=doc,
    )


def _filing_page(filing: dict) -> str:
    return FILING_PAGE.format(
        cik_int=int(filing["cik"]),
        acc_nodash=filing["accession"].replace("-", ""),
        acc=filing["accession"],
    )


def fetch_form4(filing: dict) -> bytes:
    cached = cache_path("sec", f"{filing['accession']}.xml")
    if cached.exists():
        return cached.read_bytes()
    raw = get(_xml_url(filing), limiter=SEC_LIMITER).content
    cached.write_bytes(raw)
    return raw


def parse_form4(xml_bytes: bytes, filing: dict) -> list[dict]:
    """Parse one Form 4 into zero or more transaction records."""
    root = ET.fromstring(xml_bytes)

    issuer = _text(root, ".//issuerName") or filing.get("issuer_name")
    ticker = _text(root, ".//issuerTradingSymbol")
    owner = _text(root, ".//rptOwnerName") or "Unknown"

    roles = []
    if _text(root, ".//isDirector") in ("1", "true"):
        roles.append("Director")
    if _text(root, ".//isOfficer") in ("1", "true"):
        roles.append(_text(root, ".//officerTitle") or "Officer")
    if _text(root, ".//isTenPercentOwner") in ("1", "true"):
        roles.append("10% owner")
    role = ", ".join(roles) or "Insider"

    footnotes = {
        fn.get("id"): (fn.text or "").strip()
        for fn in root.findall(".//footnote")
        if fn.get("id")
    }

    disclosure_date = filing["filing_date"]
    period_of_report = parse_date(_text(root, ".//periodOfReport") or "")
    records: list[dict] = []

    for kind, tag in (("non-derivative", "nonDerivativeTransaction"),
                      ("derivative", "derivativeTransaction")):
        for i, tx in enumerate(root.findall(f".//{tag}")):
            code = _text(tx, "transactionCoding/transactionCode")
            action, code_label, copyable = TX_CODES.get(
                code or "", ("other", f"Code {code}", False)
            )
            acq_disp = _text(
                tx, "transactionAmounts/transactionAcquiredDisposedCode/value"
            )
            shares = _float(tx, "transactionAmounts/transactionShares/value")
            price = _float(tx, "transactionAmounts/transactionPricePerShare/value")
            trade_date = parse_date(_text(tx, "transactionDate/value") or "")

            # Value is only meaningful when both numbers are actually reported.
            value = round(shares * price, 2) if (shares and price) else None

            note_ids = [
                el.get("id")
                for el in tx.iter()
                if el.tag == "footnoteId" and el.get("id")
            ]
            notes = [footnotes[n] for n in note_ids if n in footnotes]

            # A Form 4 is due within two business days, so a long gap means the
            # filing contradicts itself - almost always a mistyped year. Report
            # the date exactly as filed and flag it; never quietly "fix" it.
            lag = delay_days(trade_date, disclosure_date)
            anomaly = None
            if lag is not None and lag > 10:
                anomaly = (
                    f"Filing reports a trade date {lag} days before its filing date, "
                    f"but a Form 4 is due within 2 business days"
                )
                if period_of_report and trade_date and period_of_report != trade_date:
                    anomaly += f"; period of report says {period_of_report}"
            elif lag is not None and lag < 0:
                anomaly = "Filing reports a trade date after its own filing date"

            records.append(
                {
                    "id": f"sec4-{filing['accession']}-{kind[:3]}-{i + 1}",
                    "source": SOURCE_ID,
                    "source_label": SOURCE_LABEL,
                    "person": owner,
                    "person_id": slug(owner),
                    "role": f"{role} @ {ticker or issuer}",
                    "issuer": issuer,
                    "ticker": ticker,
                    "asset_name": _text(tx, "securityTitle/value"),
                    "security_kind": kind,
                    "action": action,
                    "transaction_code": code,
                    "transaction_code_label": code_label,
                    # Only P and S could actually have been copied.
                    "copyable": copyable,
                    "acquired_or_disposed": acq_disp,
                    "shares": shares,
                    "price_per_share": price,
                    "value_usd": value,
                    "shares_owned_after": _float(
                        tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                    ),
                    "ownership": _text(tx, "ownershipNature/directOrIndirectOwnership/value"),
                    "trade_date": trade_date,
                    "period_of_report": period_of_report,
                    "disclosure_date": disclosure_date,
                    "delay_days": lag,
                    "date_anomaly": anomaly,
                    "footnotes": notes,
                    "doc_id": filing["accession"],
                    "form": filing["form"],
                    "amended": filing["form"].endswith("/A"),
                    "source_url": _filing_page(filing),
                    "source_xml_url": _xml_url(filing),
                    "parsed": True,
                }
            )
    return records


def form144_card(filing: dict) -> dict:
    """Form 144 announces an intent to sell. Linked, not parsed as a trade."""
    return {
        "id": f"sec144-{filing['accession']}",
        "source": "sec_form144",
        "source_label": "SEC EDGAR - Form 144 (notice of proposed sale)",
        "person": None,
        "issuer": filing.get("issuer_name"),
        "ticker": (filing.get("issuer_tickers") or [None])[0],
        "disclosure_date": filing["filing_date"],
        "doc_id": filing["accession"],
        "form": filing["form"],
        "source_url": _filing_page(filing),
        "parsed": False,
        "unparsed_reason": "notice_only",
        "unparsed_detail": "Form 144 is a notice of intent to sell, not a completed trade.",
    }


def run(limit_per_company: int | None = None) -> list[dict]:
    cfg = load_watchlist()
    companies = cfg.get("companies", []) or []

    records: list[dict] = []
    errors = 0
    codes: Counter[str] = Counter()

    for company in companies:
        cik = company["cik"]
        try:
            filings = list_filings(cik)
        except Exception as exc:
            errors += 1
            update_status(SOURCE_ID, ok=False, detail=f"{company['name']}: {exc}")
            continue

        form4s = [f for f in filings if f["form"].startswith("4")]
        form144s = [f for f in filings if f["form"] == "144"]
        if limit_per_company:
            form4s = form4s[:limit_per_company]
            form144s = form144s[:limit_per_company]

        for filing in form4s:
            try:
                parsed = parse_form4(fetch_form4(filing), filing)
                records.extend(parsed)
                codes.update(r["transaction_code"] for r in parsed)
            except Exception as exc:
                errors += 1
                records.append(
                    {
                        "id": f"sec4-{filing['accession']}-err",
                        "source": SOURCE_ID,
                        "source_label": SOURCE_LABEL,
                        "issuer": filing.get("issuer_name"),
                        "disclosure_date": filing["filing_date"],
                        "doc_id": filing["accession"],
                        "source_url": _filing_page(filing),
                        "parsed": False,
                        "unparsed_reason": "parse_error",
                        "unparsed_detail": str(exc)[:200],
                    }
                )

        records.extend(form144_card(f) for f in form144s)

    write_json("sec_form4.json", records)
    tradeable = sum(1 for r in records if r.get("copyable"))
    update_status(
        SOURCE_ID,
        ok=errors == 0,
        detail=(
            f"{len(records)} rows, {tradeable} open-market (P/S), "
            f"{errors} errors; codes={dict(codes)}"
        ),
        count=len(records),
    )
    return records


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    out = run(limit_per_company=lim)
    codes = Counter(r.get("transaction_code") for r in out if r.get("parsed"))
    print(f"{len(out)} rows; open-market P/S = {sum(1 for r in out if r.get('copyable'))}")
    print("transaction codes:", dict(codes))
