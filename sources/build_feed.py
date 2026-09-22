"""Merge every source into the files the PWA reads.

Outputs
    data/trades.json  one normalised record per transaction or document
    data/people.json  per-person rollup, including positions where reported
    data/meta.json    last-updated, per-source health, and counts

Duplicates are marked, never silently dropped. An amended filing that repeats
a trade already disclosed is flagged `superseded` and hidden from the default
feed, but the record stays so the history remains auditable.
"""
from __future__ import annotations

import datetime as dt

from common import now_iso, read_json, write_json

SOURCE_FILES = {
    "house_ptr": "house_ptr.json",
    "sec_form4": "sec_form4.json",
    "oge_278t": "oge_278t.json",
    "crypto": "crypto.json",
}

# Asset-type codes that are not copyable instruments, whatever the filing says.
NON_COPYABLE_ASSET_TYPES = {
    "GS",  # government securities: municipal bonds, Treasuries
    "CS",  # corporate securities / certificates of deposit
    "OP",  # options - copying an option is not copying the stock
}


def _is_option(record: dict) -> bool:
    if (record.get("asset_type") or "").upper() == "OP":
        return True
    note = (record.get("filer_note") or "") + " " + (record.get("asset_name") or "")
    return bool(note) and any(
        word in note.lower() for word in ("call option", "put option", "strike price")
    )


def normalise(record: dict) -> dict:
    """Give every source the same shape, and decide copyability once."""
    out = dict(record)
    out.setdefault("parsed", False)
    out.setdefault("amended", False)
    out.setdefault("withdrawn", False)

    reasons: list[str] = []
    if not out.get("parsed"):
        reasons.append("document not machine-parsed")
    if out.get("withdrawn"):
        reasons.append("withdrawn by the filer in an amendment")
    if out.get("action") not in ("buy", "sell"):
        if out.get("parsed"):
            reasons.append(f"not an open-market trade ({out.get('action') or 'n/a'})")
    # Form 4 decides copyability from its transaction code.
    if out.get("source") == "sec_form4" and out.get("copyable") is False:
        reasons.append(f"Form 4 code {out.get('transaction_code')} is not a trade")
    if out.get("likely_airdrop"):
        reasons.append("unsolicited airdrop, not an acquisition")
    if _is_option(out):
        reasons.append("option contract, not the underlying share")
    if (out.get("asset_type") or "").upper() in NON_COPYABLE_ASSET_TYPES and not _is_option(out):
        reasons.append("bond or deposit, not a listed security")
    if out.get("parsed") and not out.get("ticker"):
        reasons.append("no ticker reported")

    out["copy_eligible"] = not reasons
    out["copy_ineligible_reasons"] = reasons

    # Some filings state outright that the filer did not choose the trade. It
    # is still copyable, but it says nothing about the filer's judgement, so it
    # must be visible rather than buried.
    endnote = (out.get("endnote") or "").lower()
    out["advisor_directed"] = bool(endnote) and (
        "independent advisor" in endnote or "without personal input" in endnote
    )
    return out


def dedupe(records: list[dict]) -> list[dict]:
    """Flag repeats of the same trade across different documents.

    Two rows in the SAME document that look alike are kept as-is: filers do
    legitimately report several identical transactions across different
    accounts. Only a repeat appearing in a DIFFERENT document is treated as a
    re-disclosure, and the earliest disclosure is the one that counts.
    """
    buckets: dict[tuple, list[dict]] = {}
    for record in records:
        if not record.get("parsed"):
            continue
        key = (
            record.get("person_id"),
            (record.get("ticker") or record.get("asset_name") or "").lower()[:60],
            record.get("action"),
            record.get("trade_date"),
            record.get("amount_label") or record.get("shares"),
        )
        buckets.setdefault(key, []).append(record)

    for group in buckets.values():
        if len(group) < 2:
            continue
        by_doc: dict[str, list[dict]] = {}
        for record in group:
            by_doc.setdefault(record.get("doc_id") or "", []).append(record)
        if len(by_doc) < 2:
            continue  # all from one document: genuinely separate transactions

        # Earliest disclosure wins; later repeats are marked superseded.
        ordered = sorted(
            group, key=lambda r: (r.get("disclosure_date") or "9999", r.get("id") or "")
        )
        first = ordered[0]
        for later in ordered[1:]:
            if later.get("doc_id") == first.get("doc_id"):
                continue
            later["superseded"] = True
            later["superseded_by"] = first.get("id")
            later["copy_eligible"] = False
            later.setdefault("copy_ineligible_reasons", []).append(
                "re-disclosure of an earlier filing"
            )
    return records


def build() -> dict:
    records: list[dict] = []
    per_source_counts: dict[str, int] = {}

    for source, filename in SOURCE_FILES.items():
        loaded = read_json(filename, default=[]) or []
        per_source_counts[source] = len(loaded)
        records.extend(normalise(r) for r in loaded)

    records = dedupe(records)

    def sort_key(record: dict):
        return (
            record.get("disclosure_date") or "",
            record.get("trade_date") or "",
            record.get("id") or "",
        )

    records.sort(key=sort_key, reverse=True)
    write_json("trades.json", records)

    people = build_people(records)
    write_json("people.json", people)

    statuses = read_json("status.json", default={}) or {}
    parsed = [r for r in records if r.get("parsed")]
    meta = {
        "generated_at": now_iso(),
        "counts": {
            "records": len(records),
            "parsed_trades": len(parsed),
            "linked_documents": len(records) - len(parsed),
            "copy_eligible": sum(1 for r in records if r.get("copy_eligible")),
            "people": len(people),
            "by_source": per_source_counts,
        },
        "sources": statuses,
    }
    write_json("meta.json", meta)
    return meta


def build_people(records: list[dict]) -> list[dict]:
    """Roll trades up per person, carrying positions only where reported."""
    people: dict[str, dict] = {}

    for record in records:
        pid = record.get("person_id")
        if not pid:
            continue
        person = people.setdefault(
            pid,
            {
                "person_id": pid,
                "name": record.get("person"),
                "roles": [],
                "sources": [],
                "trade_count": 0,
                "document_count": 0,
                "first_seen": None,
                "last_seen": None,
                "positions": [],
            },
        )
        if record.get("role") and record["role"] not in person["roles"]:
            person["roles"].append(record["role"])
        if record.get("source") and record["source"] not in person["sources"]:
            person["sources"].append(record["source"])

        if record.get("parsed"):
            person["trade_count"] += 1
        else:
            person["document_count"] += 1

        for field in ("first_seen", "last_seen"):
            date = record.get("disclosure_date")
            if not date:
                continue
            if person["first_seen"] is None or date < person["first_seen"]:
                person["first_seen"] = date
            if person["last_seen"] is None or date > person["last_seen"]:
                person["last_seen"] = date

    # Positions come only from Form 4's "shares owned following transaction".
    # No other source reports holdings, so for everyone else it stays empty and
    # the UI says "not disclosed".
    latest: dict[tuple, dict] = {}
    for record in records:
        if record.get("source") != "sec_form4" or not record.get("parsed"):
            continue
        if record.get("shares_owned_after") is None:
            continue
        key = (record.get("person_id"), record.get("ticker"), record.get("security_kind"))
        current = latest.get(key)
        stamp = (record.get("trade_date") or "", record.get("disclosure_date") or "")
        if current is None or stamp > current["_stamp"]:
            latest[key] = {
                "_stamp": stamp,
                "ticker": record.get("ticker"),
                "security": record.get("asset_name"),
                "security_kind": record.get("security_kind"),
                "shares_owned": record.get("shares_owned_after"),
                "as_of_trade_date": record.get("trade_date"),
                "as_of_disclosure_date": record.get("disclosure_date"),
                "ownership": record.get("ownership"),
                "source_url": record.get("source_url"),
            }

    for (pid, _, _), position in latest.items():
        if pid in people:
            position.pop("_stamp", None)
            people[pid]["positions"].append(position)

    out = sorted(people.values(), key=lambda p: -(p["trade_count"] + p["document_count"]))
    return out


if __name__ == "__main__":
    summary = build()
    counts = summary["counts"]
    print(f"generated {summary['generated_at']}")
    print(f"  records          : {counts['records']}")
    print(f"  parsed trades    : {counts['parsed_trades']}")
    print(f"  linked documents : {counts['linked_documents']}")
    print(f"  copy-eligible    : {counts['copy_eligible']}")
    print(f"  people           : {counts['people']}")
    print(f"  by source        : {counts['by_source']}")
