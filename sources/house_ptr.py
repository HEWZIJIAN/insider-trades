"""U.S. House Clerk - Periodic Transaction Reports (PTRs).

Pipeline
    1. Download the Clerk's annual index ZIP (refreshed daily).
    2. Keep rows where FilingType == 'P' (a PTR).
    3. Filter to the watchlist.
    4. Download each PTR PDF and extract its text with pypdf (pure Python, so
       local and CI produce byte-identical results).
    5. Parse transaction rows.

About 12% of PTRs are scanned paper filings with no text layer. Those are
emitted as `parsed: false` document cards linking to the original PDF. They
are never guessed at.

Primary source: https://disclosures-clerk.house.gov/PublicDisclosure
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import sys
import zipfile
from dataclasses import dataclass

from pypdf import PdfReader

from common import (
    BROWSER_UA,  # noqa: F401  (documents why the Clerk fetch uses a browser UA)
    cache_path,
    delay_days,
    get,
    load_watchlist,
    parse_date,
    parse_money,
    reusable_records,
    slug,
    update_status,
    write_json,
)

SOURCE_ID = "house_clerk_ptr"
# Bump when parsing changes, to reprocess filings already stored.
PARSER_VERSION = "2026-09-22.5"   # inline rows + comment-leak fix
SOURCE_LABEL = "U.S. House Clerk - Periodic Transaction Report"
INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# Transaction type codes used on the House PTR form.
TX_TYPES = {
    "P": "buy",
    "S": "sell",
    "E": "exchange",
}

# A transaction row always ENDS with: <type> <date> <date> <amount...>
#
# Where it starts varies. Usually the asset name occupies the preceding lines
# and the row stands alone:
#     Sony Group Corporation American
#     Depositary Shares (SONY) [ST]
#     P 12/26/2026 01/21/2026 $1,001 - $15,000
#
# But often the tail shares its line with the end of the asset name:
#     AT&T Inc. (T) [ST] S (partial) 07/02/2025 08/11/2025 $1,001 - $15,000
#
# So this is searched for at the END of a line rather than anchored at the
# start, and whatever precedes it on that line is part of the asset name.
# Anchoring at the start silently dropped 541 transactions across 146 filings.
TX_ANCHOR = re.compile(
    r"""(?:^|\s)
    (?P<type>P|S|E)
    (?:\s*\(partial\))?
    \s+(?P<trade>\d{2}/\d{2}/\d{4})
    \s+(?P<disclosed>\d{2}/\d{2}/\d{4})
    \s+(?P<amount>\$[\d,]+(?:\s*-\s*(?:\$[\d,]+)?)?)
    \s*$""",
    re.VERBOSE,
)

TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*$")
ASSET_TYPE = re.compile(r"\[([A-Z]{2,4})\]")
OWNER_PREFIX = re.compile(r"^(SP|DC|JT)\s+")
# Some filings prefix each row with the Clerk's internal transaction id.
TX_ID_PREFIX = re.compile(r"^(\d{6,})\s+")

# "F S:" is Filing Status, "S O:" is Subholding Of, "D:" is the filer's own
# note (e.g. "Asset acquired through a S&P Global (SPGI) spinoff"). All follow
# the transaction row they belong to.
FILING_STATUS = re.compile(r"^F\s*S\s*:\s*(.+)$", re.IGNORECASE)
SUBHOLDING = re.compile(r"^S\s*O\s*:\s*(.+)$", re.IGNORECASE)
FILER_NOTE = re.compile(r"^D\s*:\s*(.+)$")

# Lines that belong to a transaction block but carry no data we keep.
NOISE = re.compile(
    r"^\s*(F\s*S\s*:|S\s*O\s*:|D\s*:|C\s*:|\*\s*For the complete list|"
    r"Digitally Signed|Filing ID|ID\s+Owner\s+Asset|Type\b|Date\b|Amount\b|"
    r"Gains\s*>|\$200\?|Clerk of the House|Name:|Status:|State/District:|"
    r"[A-Z]\s*$|Yes\s+No|I CERTIFY|my knowledge)",
    re.IGNORECASE,
)


@dataclass
class Filing:
    doc_id: str
    last: str
    first: str
    district: str
    filing_date: str
    year: int

    @property
    def person(self) -> str:
        return f"{self.first} {self.last}".strip()

    @property
    def person_id(self) -> str:
        return slug(f"{self.first}-{self.last}-{self.district}")

    @property
    def pdf_url(self) -> str:
        return PDF_URL.format(year=self.year, doc_id=self.doc_id)


def fetch_index(year: int) -> list[Filing]:
    """Download and parse the Clerk's annual index of all filings."""
    resp = get(INDEX_URL.format(year=year), browser_ua=True)
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
    lines = zf.read(name).decode("utf-8", "replace").splitlines()

    filings: list[Filing] = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 9 or cols[4].strip() != "P":
            continue  # 'P' == Periodic Transaction Report
        filings.append(
            Filing(
                doc_id=cols[8].strip(),
                last=cols[1].strip(),
                first=cols[2].strip(),
                district=cols[5].strip(),
                filing_date=parse_date(cols[7].strip()) or "",
                year=year,
            )
        )
    return filings


def select(filings: list[Filing], cfg: dict) -> list[Filing]:
    """Apply the watchlist."""
    conf = cfg.get("congress", {}) or {}
    if conf.get("track_all"):
        return filings

    wanted = conf.get("members", []) or []
    out = []
    for f in filings:
        for m in wanted:
            if f.last.lower() != str(m.get("last", "")).lower():
                continue
            district = str(m.get("district", "") or "").strip()
            if district and district.upper() != f.district.upper():
                continue
            out.append(f)
            break
    return out


def extract_text(pdf_bytes: bytes) -> str:
    """Pure-Python text extraction.

    The Clerk's e-filed PDFs put their content inside a Form XObject, which
    pypdf's default mode reads correctly. Null bytes appear where the form uses
    a small-caps font with a partial encoding; they carry no information.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # a damaged page must not kill the whole filing
            continue
    return "\n".join(parts).replace("\x00", "")


# Filers write multi-line notes under "D:" (description) and "C:" (comments).
# Only the first line carries the marker; the continuations look like ordinary
# text and would otherwise be swallowed into the NEXT asset's name, producing
# entries like "calls to prevent insider trading. SP Pinterest, Inc. ...".
#
# Every asset name ends with its type marker - "(PINS) [ST]" - and comment prose
# never contains one. So the name is the run of lines ending at that marker,
# walked back until a line that closes a sentence.
NAME_ABBREV_END = re.compile(
    r"\b(Inc|Corp|Co|Ltd|plc|LP|L\.P|LLC|N\.V|S\.A|A\.G|Cos|Bros|Intl|Sr|Jr)\.$",
    re.IGNORECASE,
)

# Prose that does not end in a full stop still is not part of a security name.
# Filers narrate their trades: "... sold @ $493.42/share SPY - 8.318 shares".
COMMENT_PROSE = re.compile(
    r"(@\s*\$|/share|\bshares sold\b|\bI directed\b|\bpurchased those\b|"
    r"\bthis entry\b|\bcan only be sold\b)",
    re.IGNORECASE,
)


def _asset_lines(pending: list[str], head: str) -> list[str]:
    """Pick out just the asset-name lines from everything seen since the last row."""
    lines = [l for l in (list(pending) + ([head] if head else [])) if l]
    if not lines:
        return []

    marker = None
    for i in range(len(lines) - 1, -1, -1):
        if ASSET_TYPE.search(lines[i]):
            marker = i
            break
    if marker is None:
        # No type marker in this block; keep the tail and hope for the best.
        return lines[-3:]

    start = marker
    while start > 0:
        previous = lines[start - 1]
        ends_sentence = previous.endswith((".", ":", ";")) and not NAME_ABBREV_END.search(previous)
        if ends_sentence or COMMENT_PROSE.search(previous):
            break
        start -= 1
    return lines[start: marker + 1]


def _clean_asset(lines: list[str]) -> str:
    text = " ".join(l.strip() for l in lines if l.strip())
    text = ASSET_TYPE.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" .,-")


def parse_transactions(text: str, filing: Filing) -> list[dict]:
    """Walk the extracted text in reading order and pull out transaction rows."""
    lines = text.splitlines()
    trades: list[dict] = []
    pending: list[str] = []  # candidate asset-name lines seen since the last row

    for idx, line in enumerate(lines):
        match = TX_ANCHOR.search(line)
        if not match:
            stripped = line.strip()
            if stripped and not NOISE.match(stripped):
                pending.append(stripped)
                # Long security names really do run to five or six lines, e.g.
                # "Alphabet Inc. - Depositary Shares representing a 1/20th
                # Interest in a Share of Series B Mandatory Convertible
                # Preferred Stock". Dropping the first line loses both the
                # start of the name and the owner code that sits on it.
                # Contamination is prevented by the NOISE clear below, which
                # fires between every pair of transactions.
                if len(pending) > 8:
                    pending.pop(0)
            elif NOISE.match(stripped):
                pending.clear()
            continue

        raw_amount = match.group("amount").strip()
        # The upper bound of a range routinely wraps onto the next line.
        if raw_amount.endswith("-") and idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if re.fullmatch(r"\$[\d,]+", nxt):
                raw_amount = f"{raw_amount} {nxt}"

        amount_label = re.sub(r"\s+", " ", raw_amount).strip()
        bounds = re.findall(r"\$[\d,]+", amount_label)
        amount_min = parse_money(bounds[0]) if bounds else None
        amount_max = parse_money(bounds[1]) if len(bounds) > 1 else None

        # Anything before the tail on this same line is the end of the asset
        # name, e.g. "AT&T Inc. (T) [ST] S (partial) 07/02/2025 ...".
        head = line[: match.start("type")].strip()
        asset_lines = _asset_lines(pending, head)
        owner = "self"
        tx_id = None
        if asset_lines:
            id_match = TX_ID_PREFIX.match(asset_lines[0])
            if id_match:
                tx_id = id_match.group(1)
                asset_lines[0] = TX_ID_PREFIX.sub("", asset_lines[0])
            owner_match = OWNER_PREFIX.match(asset_lines[0])
            if owner_match:
                owner = {"SP": "spouse", "DC": "dependent_child", "JT": "joint"}[
                    owner_match.group(1)
                ]
                asset_lines[0] = OWNER_PREFIX.sub("", asset_lines[0])

        # Filing status and subholding follow the row. "Deleted" means the filer
        # withdrew this transaction in an amendment - it is not a real trade.
        filing_status, subholding, filer_note = None, None, None
        for look in lines[idx + 1: idx + 8]:
            stripped_look = look.strip()
            if TX_ANCHOR.match(look):
                break  # we have reached the next transaction
            status_match = FILING_STATUS.match(stripped_look)
            if status_match and filing_status is None:
                filing_status = status_match.group(1).strip()
            sub_match = SUBHOLDING.match(stripped_look)
            if sub_match and subholding is None:
                subholding = sub_match.group(1).strip()
            note_match = FILER_NOTE.match(stripped_look)
            if note_match and filer_note is None:
                filer_note = note_match.group(1).strip()

        asset_raw = " ".join(asset_lines)
        ticker_match = TICKER.search(_clean_asset(asset_lines))
        asset_type_match = ASSET_TYPE.search(asset_raw)
        asset_name = _clean_asset(asset_lines)
        ticker = None
        if ticker_match:
            ticker = ticker_match.group(1)
            asset_name = asset_name[: ticker_match.start()].strip(" .,-")

        trade_date = parse_date(match.group("trade"))
        disclosure_date = parse_date(match.group("disclosed"))

        # Filings do contain impossible dates: one reports a trade on
        # 12/26/2026 disclosed 01/21/2026, and was signed in February 2026.
        # Almost always a mistyped year. Publish the date exactly as filed and
        # say that it contradicts itself - never quietly correct it.
        lag = delay_days(trade_date, disclosure_date or filing.filing_date)
        anomaly = None
        if lag is not None and lag < 0:
            anomaly = (
                f"Filing reports a trade date {abs(lag)} days AFTER the date it "
                f"was disclosed, which cannot be right"
            )
        elif trade_date and trade_date > _dt.date.today().isoformat():
            anomaly = "Filing reports a trade date in the future"

        trades.append(
            {
                "id": f"house-{filing.doc_id}-{len(trades) + 1}",
                "source": SOURCE_ID,
                "source_label": SOURCE_LABEL,
                "person": filing.person,
                "person_id": filing.person_id,
                "role": f"House ({filing.district})",
                "owner": owner,
                "asset_name": asset_name or None,
                "ticker": ticker,
                "asset_type": asset_type_match.group(1) if asset_type_match else None,
                "action": TX_TYPES.get(match.group("type")),
                "amount_min": amount_min,
                "amount_max": amount_max,
                "amount_label": amount_label,  # exactly as reported
                "trade_date": trade_date,
                "disclosure_date": disclosure_date or filing.filing_date,
                "delay_days": lag,
                "date_anomaly": anomaly,
                "doc_id": filing.doc_id,
                "tx_id": tx_id,
                "filing_status": filing_status,
                "subholding": subholding,
                "filer_note": filer_note,
                # A withdrawn row stays visible but must never count as a trade.
                "withdrawn": (filing_status or "").lower() == "deleted",
                "source_url": filing.pdf_url,
                "parsed": True,
            }
        )
        pending.clear()

    return trades


def unparsed_card(filing: Filing, reason: str, detail: str = "") -> dict:
    """A filing we could not read. Shown as a document, never as invented rows."""
    return {
        "id": f"house-{filing.doc_id}-doc",
        "source": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "person": filing.person,
        "person_id": filing.person_id,
        "role": f"House ({filing.district})",
        "disclosure_date": filing.filing_date,
        "doc_id": filing.doc_id,
        "source_url": filing.pdf_url,
        "parsed": False,
        "unparsed_reason": reason,
        "unparsed_detail": detail,
    }


def fetch_filing(filing: Filing) -> tuple[list[dict], list[dict]]:
    """Return (trades, unparsed_cards) for one PTR, using an on-disk cache."""
    cached = cache_path("house", f"{filing.year}-{filing.doc_id}.pdf")
    if cached.exists():
        pdf_bytes = cached.read_bytes()
    else:
        pdf_bytes = get(filing.pdf_url, browser_ua=True).content
        cached.write_bytes(pdf_bytes)

    if not pdf_bytes.startswith(b"%PDF"):
        return [], [unparsed_card(filing, "not_a_pdf")]

    text = extract_text(pdf_bytes)
    # A scanned filing yields essentially nothing. Threshold is generous: the
    # shortest real e-filed PTR we have seen is ~900 characters.
    if len(text.strip()) < 200:
        return [], [unparsed_card(filing, "scanned_image",
                                  f"{len(text.strip())} characters of text")]

    trades = parse_transactions(text, filing)
    if not trades:
        return [], [unparsed_card(filing, "no_rows_parsed",
                                  f"{len(text)} characters extracted")]
    return trades, []


def run(year: int | None = None, limit: int | None = None) -> list[dict]:
    import datetime

    cfg = load_watchlist()
    year = year or datetime.date.today().year

    reusable, done = reusable_records("house_ptr.json", "doc_id", PARSER_VERSION)
    filings = fetch_index(year)
    selected = select(filings, cfg)
    selected.sort(key=lambda f: f.filing_date, reverse=True)
    if limit:
        selected = selected[:limit]

    trades: list[dict] = []
    unparsed: list[dict] = []
    errors = 0
    reused = 0
    for filing in selected:
        if filing.doc_id in done:
            for record in reusable[filing.doc_id]:
                (trades if record.get("parsed") else unparsed).append(record)
            reused += 1
            continue
        try:
            got, missed = fetch_filing(filing)
            trades.extend(got)
            unparsed.extend(missed)
        except Exception as exc:
            errors += 1
            unparsed.append(unparsed_card(filing, "fetch_error", str(exc)[:200]))

    records = trades + unparsed
    for record in records:
        record["parser_version"] = PARSER_VERSION
    write_json("house_ptr.json", records)
    update_status(
        SOURCE_ID,
        ok=errors == 0,
        detail=(
            f"{len(selected)} filings ({reused} reused), {len(trades)} trades "
            f"parsed, {len(unparsed)} unparsed, {errors} errors"
        ),
        count=len(trades),
    )
    return records


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    out = run(limit=lim)
    parsed = [r for r in out if r.get("parsed")]
    print(f"{len(parsed)} trades parsed, {len(out) - len(parsed)} unparsed documents")
