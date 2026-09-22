"""Push a notification when someone on the watchlist files a new trade.

Uses ntfy.sh: free, no account, and it has an iOS app. The topic name is the
only secret, so it comes from the NTFY_TOPIC environment variable (a repo
secret) and never appears in the code or the committed config.

Anyone who knows the topic name can read your alerts, so use something long
and unguessable.

State lives in data/seen_ids.json, which the scheduled job commits. On the very
first run that file does not exist; we record the baseline silently rather than
firing several hundred notifications at once.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

import requests

from common import DATA, load_watchlist, now_iso, read_json, update_status, write_json

SOURCE_ID = "alerts"
NTFY_BASE = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
SEEN_FILE = "seen_ids.json"
MAX_PER_RUN = 12  # beyond this, send one summary instead of a burst


def _site_url() -> str:
    return os.environ.get("SITE_URL", "").rstrip("/")


def _headline(trade: dict) -> tuple[str, str]:
    person = trade.get("person") or trade.get("issuer") or "Someone"

    if not trade.get("parsed"):
        return (f"{person} — new filing", "A filing that could not be machine-parsed. Open it to read.")

    action = (trade.get("action") or "").upper()
    ticker = trade.get("ticker") or (trade.get("asset_name") or "")[:30]

    if trade.get("amount_label"):
        size = trade["amount_label"]
    elif trade.get("shares") is not None:
        size = f"{trade['shares']:,.0f} shares"
    else:
        size = ""

    title = f"{person}: {action} {ticker}".strip()
    bits = [size] if size else []
    if trade.get("trade_date"):
        bits.append(f"traded {trade['trade_date']}")
    if trade.get("delay_days") is not None:
        bits.append(f"disclosed after {trade['delay_days']}d")
    if trade.get("advisor_directed"):
        bits.append("advisor-directed, not the filer's own decision")
    if not trade.get("copy_eligible") and trade.get("copy_ineligible_reasons"):
        bits.append(f"not scored: {trade['copy_ineligible_reasons'][0]}")
    return title, " · ".join(bits)


def passes_amount_threshold(trade: dict, minimum: float, alert_unreadable: bool) -> tuple[bool, str]:
    """Is this trade big enough to be worth a notification?

    Filings disclose a BAND, not a figure, so the test is on the bottom of the
    band: a trade qualifies if it could be at least `minimum`. That errs
    towards telling you, which is the right way round for an alert.

    Returns (passes, reason) so the run can report what it held back rather
    than silently swallowing things.
    """
    if minimum <= 0:
        return True, "no threshold set"

    if not trade.get("parsed"):
        return alert_unreadable, "filing could not be read, so it has no amount"

    # On-chain swaps have a token quantity, not a dollar value. They are rare
    # and deliberate, so they always notify rather than being judged on a
    # number we do not have.
    if trade.get("source") == "crypto":
        if trade.get("likely_airdrop"):
            return False, "unsolicited airdrop"
        if trade.get("is_swap"):
            return True, "on-chain swap (no USD value available)"
        return False, "on-chain transfer, not a trade"

    # Range-based filings: the bottom of the band.
    if trade.get("amount_min") is not None:
        ok = trade["amount_min"] >= minimum
        return ok, f"band starts at ${trade['amount_min']:,}"

    # Form 4 reports an exact value.
    if trade.get("value_usd") is not None:
        ok = trade["value_usd"] >= minimum
        return ok, f"reported value ${trade['value_usd']:,.0f}"

    return alert_unreadable, "no amount reported"


def _watchlisted(trade: dict, cfg: dict) -> bool:
    """Does this record belong to someone the watchlist names?"""
    congress = cfg.get("congress", {}) or {}
    if congress.get("track_all"):
        return True
    person = (trade.get("person") or "").lower()

    for member in congress.get("members", []) or []:
        if str(member.get("last", "")).lower() in person:
            return True

    execs = cfg.get("executive_branch", {}) or {}
    if execs.get("track_all"):
        return True
    for name in execs.get("highlight", []) or []:
        if str(name).lower() in person:
            return True

    # Company insiders are on the list by virtue of the company being tracked.
    return trade.get("source") in ("sec_form4", "sec_form144", "crypto")


def send(topic: str, title: str, message: str, *, click: str = "", tags: str = "chart_with_upwards_trend") -> bool:
    headers = {
        "Title": title.encode("utf-8", "replace").decode("latin-1", "replace"),
        "Tags": tags,
        "Priority": "default",
    }
    if click:
        headers["Click"] = click
    try:
        resp = requests.post(
            f"{NTFY_BASE}/{urllib.parse.quote(topic)}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=20,
        )
        return resp.status_code < 300
    except requests.RequestException:
        return False


def run(dry_run: bool = False) -> dict:
    cfg = load_watchlist()
    alerts_cfg = cfg.get("alerts", {}) or {}
    topic = os.environ.get("NTFY_TOPIC", "").strip()

    trades = read_json("trades.json", default=[]) or []
    current_ids = [t["id"] for t in trades if t.get("id")]

    seen_state = read_json(SEEN_FILE, default=None)
    first_run = seen_state is None

    seen = set((seen_state or {}).get("ids", []))
    new = [t for t in trades if t.get("id") and t["id"] not in seen]

    if alerts_cfg.get("only_watchlisted", True):
        new = [t for t in new if _watchlisted(t, cfg)]

    # Apply the size threshold, keeping a count of what was held back so the
    # Sources tab can say so rather than leaving it invisible.
    minimum = float(alerts_cfg.get("min_amount_usd") or 0)
    alert_unreadable = bool(alerts_cfg.get("alert_on_unreadable_filings", False))
    held_back = 0
    if minimum > 0:
        kept = []
        for trade in new:
            ok, _ = passes_amount_threshold(trade, minimum, alert_unreadable)
            if ok:
                kept.append(trade)
            else:
                held_back += 1
        new = kept

    # Newest first, so a truncated burst shows the most recent.
    new.sort(key=lambda t: (t.get("disclosure_date") or "", t.get("id") or ""), reverse=True)

    sent = 0
    skipped_reason = None

    if first_run:
        skipped_reason = "first run - recording baseline instead of alerting"
    elif not new:
        skipped_reason = "no new trades"
    elif not alerts_cfg.get("enabled", True):
        skipped_reason = "alerts disabled in watchlist.yml"
    elif not topic:
        skipped_reason = f"{len(new)} new trades but NTFY_TOPIC is not set"
    elif dry_run:
        skipped_reason = f"dry run - would have sent {len(new)}"
    else:
        site = _site_url()
        if len(new) > MAX_PER_RUN:
            people = sorted({t.get("person") or "?" for t in new})[:6]
            ok = send(
                topic,
                f"{len(new)} new disclosed trades",
                f"From {', '.join(people)}"
                + (" and others" if len(people) >= 6 else "")
                + ". Open the app to see them.",
                click=site or "",
                tags="bell",
            )
            sent = len(new) if ok else 0
        else:
            for trade in new:
                title, body = _headline(trade)
                link = trade.get("source_url") or site
                if send(topic, title, body, click=link,
                        tags="chart_with_upwards_trend" if trade.get("action") == "buy" else "chart_with_downwards_trend"):
                    sent += 1

    write_json(SEEN_FILE, {"updated_at": now_iso(), "ids": current_ids})

    detail = skipped_reason or f"sent {sent} of {len(new)} new"
    if held_back:
        detail += f"; {held_back} below the ${minimum:,.0f} alert threshold"
    update_status(SOURCE_ID, ok=True, detail=detail, count=sent)
    return {"new": len(new), "sent": sent, "held_back": held_back,
            "detail": detail, "first_run": first_run}


if __name__ == "__main__":
    result = run(dry_run="--dry-run" in sys.argv)
    print(f"new: {result['new']}  sent: {result['sent']}  "
          f"held back: {result['held_back']}  ({result['detail']})")
