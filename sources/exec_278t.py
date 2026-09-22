"""Executive branch - OGE Form 278-T, as published on whitehouse.gov/disclosures.

Two very different kinds of document live on that page:

  * Born-digital filings (most staff). Clean text, parsed normally.
  * Scanned filings, including every one of the President's own. Some carry no
    text at all; others carry an OCR layer so damaged that "Purchase" comes out
    as "ourchose", "lourchaso" or "PUrchaso", and "$500,001" loses its comma to
    become "$500 001".

The second kind is the reason this module is strict. Publishing a row means
asserting it is what the filing says, so every controlled field - the
transaction type and the amount band - must match OGE's fixed vocabulary
EXACTLY. Anything else is treated as unreadable and the document is linked
instead. We would rather show you a PDF than a plausible-looking guess.
"""
from __future__ import annotations

import html
import io
import re
import sys
from urllib.parse import urljoin

from pypdf import PdfReader

from common import (
    cache_path,
    delay_days,
    get,
    load_watchlist,
    parse_date,
    parse_money,
    slug,
    update_status,
    write_json,
)

SOURCE_ID = "oge_278t"
SOURCE_LABEL = "OGE Form 278-T (whitehouse.gov)"
DISCLOSURES_URL = "https://www.whitehouse.gov/disclosures/"

# OGE's transaction types. Anything not in this set is an extraction error.
TX_TYPES = {"purchase": "buy", "sale": "sell", "exchange": "exchange"}

# OGE's fixed amount bands (5 C.F.R. 2634). A value that is not exactly one of
# these did not come out of the PDF cleanly, so the row is not published.
AMOUNT_BANDS = {
    "$1,001 - $15,000",
    "$15,001 - $50,000",
    "$50,001 - $100,000",
    "$100,001 - $250,000",
    "$250,001 - $500,000",
    "$500,001 - $1,000,000",
    "$1,000,001 - $5,000,000",
    "$5,000,001 - $25,000,000",
    "$25,000,001 - $50,000,000",
    "Over $50,000,000",
}

PDF_LINK = re.compile(
    r'<a[^>]+href="(?P<url>[^"]*?Periodic-Transaction-Report[^"]*?\.pdf)"[^>]*>(?P<text>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# "President Donald J. Trump Periodic Transaction Report Amendment 01.14.26"
LINK_TEXT = re.compile(
    r"^(?P<name>.*?)\s*Periodic\s+Transaction\s+Report\s*(?P<amend>Amendment)?\s*"
    r"(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4})?",
    re.IGNORECASE,
)

# A parsed row, where description / type / date / flag / amount all line up.
ROW_TAIL = re.compile(
    r"(?P<type>Purchase|Sale|Exchange)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<over30>Yes|No)\s+"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|Over\s+\$[\d,]+)\s*$",
    re.IGNORECASE,
)
ROW_START = re.compile(r"^(?P<num>\d{1,3})\s+(?P<rest>.+)$")


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def list_documents() -> list[dict]:
    """Scrape the disclosures page for every 278-T link."""
    # Decode explicitly: the page separates name from title with an en dash,
    # which requests' guessed encoding turns into mojibake, and that would
    # split one person into two.
    page = get(DISCLOSURES_URL, browser_ua=True).content.decode("utf-8", "replace")
    docs: dict[str, dict] = {}

    for match in PDF_LINK.finditer(page):
        url = urljoin(DISCLOSURES_URL, html.unescape(match.group("url")))
        label = _strip_tags(match.group("text"))
        meta = LINK_TEXT.match(label)

        # Names arrive as "Zinberg, Joel -" or "Zinberg, Joel –"; drop the
        # trailing separator so one person yields one person_id.
        name = (meta.group("name") if meta else "").strip()
        name = re.sub(r"[\s\-‐-―]+$", "", name) or None
        raw_date = meta.group("date") if meta else None
        filed = None
        if raw_date:
            parts = re.split(r"[./]", raw_date)
            if len(parts) == 3:
                mm, dd, yy = parts
                yy = f"20{yy}" if len(yy) == 2 else yy
                filed = parse_date(f"{mm.zfill(2)}/{dd.zfill(2)}/{yy}")

        docs[url] = {
            "url": url,
            "label": label,
            "person": name,
            "disclosure_date": filed,
            "amended": bool(meta and meta.group("amend")),
        }
    return list(docs.values())


def select(docs: list[dict], cfg: dict) -> list[dict]:
    conf = cfg.get("executive_branch", {}) or {}
    if conf.get("track_all", True):
        return docs
    wanted = [w.lower() for w in (conf.get("highlight") or [])]
    return [d for d in docs if any(w in (d["person"] or "").lower() for w in wanted)]


def extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts).replace("\x00", "")


def parse_rows(text: str, doc: dict) -> tuple[list[dict], int]:
    """Return (rows, rejected_count).

    A row is published only when its type and amount match OGE's vocabulary
    exactly. `rejected` counts lines that looked like transactions but failed
    that check - the signal that we are looking at a bad OCR layer.
    """
    lines = [l.rstrip() for l in text.splitlines()]
    rows: list[dict] = []
    rejected = 0
    pending: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        tail = ROW_TAIL.search(stripped)
        if not tail:
            # Could be a wrapped description, or OCR noise shaped like a row.
            if re.search(r"\d{2}/\d{2}/\d{4}", stripped) and re.search(
                r"\$\s?[\d,]{3,}", stripped
            ):
                rejected += 1
                pending.clear()
            else:
                pending.append(stripped)
                if len(pending) > 4:
                    pending.pop(0)
            continue

        amount = re.sub(r"\s+", " ", tail.group("amount")).strip()
        tx_type = tail.group("type").lower()
        if amount not in AMOUNT_BANDS or tx_type not in TX_TYPES:
            rejected += 1
            pending.clear()
            continue

        # Description is whatever precedes the matched tail, on this line or
        # accumulated from the lines above it.
        head = stripped[: tail.start()].strip()
        number = None
        pieces: list[str] = []

        start = ROW_START.match(head)
        if start:
            number = start.group("num")
            pieces.append(start.group("rest").strip())
        elif head:
            pieces.append(head)

        if not pieces and pending:
            carried = list(pending)
            first = ROW_START.match(carried[0])
            if first:
                number = first.group("num")
                carried[0] = first.group("rest").strip()
            pieces = carried

        description = re.sub(r"\s+", " ", " ".join(p for p in pieces if p)).strip()
        if not description:
            rejected += 1
            pending.clear()
            continue

        ticker_match = re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*$", description)
        trade_date = parse_date(tail.group("date"))
        bounds = re.findall(r"\$[\d,]+", amount)

        rows.append(
            {
                "id": f"oge-{slug(doc['person'] or 'unknown')}-{slug(doc['disclosure_date'] or '')}-{len(rows) + 1}",
                "source": SOURCE_ID,
                "source_label": SOURCE_LABEL,
                "person": doc["person"],
                "person_id": slug(doc["person"] or "unknown"),
                "role": "Executive branch",
                "row_number": number,
                "asset_name": description,
                "ticker": ticker_match.group(1) if ticker_match else None,
                "action": TX_TYPES[tx_type],
                "amount_min": parse_money(bounds[0]) if bounds else None,
                "amount_max": parse_money(bounds[1]) if len(bounds) > 1 else None,
                "amount_label": amount,  # exactly as reported
                "notification_over_30_days": tail.group("over30").lower() == "yes",
                "trade_date": trade_date,
                "disclosure_date": doc["disclosure_date"],
                "delay_days": delay_days(trade_date, doc["disclosure_date"]),
                "amended": doc["amended"],
                "source_url": doc["url"],
                "parsed": True,
            }
        )
        pending.clear()

    return rows, rejected


def document_card(doc: dict, reason: str, detail: str = "") -> dict:
    return {
        "id": f"oge-doc-{slug(doc['label'])}",
        "source": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "person": doc["person"],
        "person_id": slug(doc["person"] or "unknown"),
        "role": "Executive branch",
        "title": doc["label"],
        "disclosure_date": doc["disclosure_date"],
        "amended": doc["amended"],
        "source_url": doc["url"],
        "parsed": False,
        "unparsed_reason": reason,
        "unparsed_detail": detail,
    }


def process(doc: dict) -> tuple[list[dict], list[dict]]:
    cached = cache_path("oge", slug(doc["url"].rsplit("/", 1)[-1]) + ".pdf")
    if cached.exists():
        pdf_bytes = cached.read_bytes()
    else:
        pdf_bytes = get(doc["url"], browser_ua=True).content
        cached.write_bytes(pdf_bytes)

    size_mb = len(pdf_bytes) / 1_000_000
    if not pdf_bytes.startswith(b"%PDF"):
        return [], [document_card(doc, "not_a_pdf")]

    text = extract_text(pdf_bytes)
    if len(text.strip()) < 200:
        return [], [
            document_card(
                doc,
                "scanned_image",
                f"scanned filing, {size_mb:.1f} MB, no readable text layer",
            )
        ]

    rows, rejected = parse_rows(text, doc)

    # Damaged OCR announces itself: many transaction-shaped lines, few or none
    # of which survive vocabulary validation.
    if rejected and rejected >= max(3, len(rows)):
        return [], [
            document_card(
                doc,
                "unreliable_text_layer",
                f"scanned filing with a damaged OCR layer: {rejected} rows "
                f"failed validation, {len(rows)} passed ({size_mb:.1f} MB)",
            )
        ]
    if not rows:
        return [], [
            document_card(doc, "no_rows_parsed", f"{len(text)} characters extracted")
        ]
    return rows, []


def run(limit: int | None = None) -> list[dict]:
    cfg = load_watchlist()
    docs = select(list_documents(), cfg)
    docs.sort(key=lambda d: d["disclosure_date"] or "", reverse=True)
    if limit:
        docs = docs[:limit]

    rows: list[dict] = []
    cards: list[dict] = []
    errors = 0
    for doc in docs:
        try:
            got, missed = process(doc)
            rows.extend(got)
            cards.extend(missed)
        except Exception as exc:
            errors += 1
            cards.append(document_card(doc, "fetch_error", str(exc)[:200]))

    records = rows + cards
    write_json("oge_278t.json", records)
    update_status(
        SOURCE_ID,
        ok=errors == 0,
        detail=(
            f"{len(docs)} documents, {len(rows)} transactions parsed, "
            f"{len(cards)} linked-only, {errors} errors"
        ),
        count=len(rows),
    )
    return records


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    out = run(limit=lim)
    parsed = [r for r in out if r.get("parsed")]
    print(f"{len(parsed)} transactions parsed, {len(out) - len(parsed)} linked-only documents")
