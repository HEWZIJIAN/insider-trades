"""Shared helpers for every source module.

Rules that apply to all sources:
  * Identify ourselves honestly in the User-Agent (SEC requires a contact address).
  * Never exceed a source's published rate limit.
  * Never invent a value. If something cannot be read, mark it unparsed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"

# Contact address sent to public data sources that ask who is calling.
# Override with CONTACT_EMAIL in the environment / repo secret.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "hewzijian9@gmail.com")
USER_AGENT = f"TrumpInsiderTracker/0.1 ({CONTACT_EMAIL})"

# Some public sites reject non-browser agents outright. Where a source needs a
# browser-style agent we still append the contact address so we stay identifiable.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 (+{CONTACT_EMAIL})"
)


class RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._interval:
                time.sleep(self._interval - delta)
            self._last = time.monotonic()


# SEC publishes a hard limit of 10 requests/second. Stay well under it.
SEC_LIMITER = RateLimiter(per_second=5)
# Everything else: be a polite guest.
POLITE_LIMITER = RateLimiter(per_second=2)


def get(
    url: str,
    *,
    limiter: RateLimiter | None = None,
    browser_ua: bool = False,
    timeout: int = 45,
    retries: int = 3,
) -> requests.Response:
    """GET with rate limiting and bounded retries. Raises on final failure."""
    (limiter or POLITE_LIMITER).wait()
    headers = {
        "User-Agent": BROWSER_UA if browser_ua else USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp
            # Back off on throttling / transient server errors; fail fast otherwise.
            if resp.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {resp.status_code} for {url}")
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:  # network-level failure
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_MONEY = re.compile(r"\$\s?([\d,]+)")


def parse_money(text: str) -> int | None:
    """'$15,001' -> 15001. Returns None if no clean number is present."""
    m = _MONEY.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_date(text: str) -> str | None:
    """'09/14/2026' -> '2026-09-14'. Returns None when unparseable."""
    text = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def delay_days(trade_date: str | None, disclosure_date: str | None) -> int | None:
    if not trade_date or not disclosure_date:
        return None
    try:
        a = _dt.date.fromisoformat(trade_date)
        b = _dt.date.fromisoformat(disclosure_date)
    except ValueError:
        return None
    return (b - a).days


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Output + per-source status
# --------------------------------------------------------------------------

def write_json(name: str, payload) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(name: str, default=None):
    path = DATA / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def update_status(source: str, *, ok: bool, detail: str = "", count: int | None = None) -> None:
    """Record per-source health so the UI can say 'unavailable, last updated X'.

    A failed run must never erase the last good timestamp - that is the whole
    point of showing staleness instead of a blank screen.
    """
    statuses = read_json("status.json", default={}) or {}
    prev = statuses.get(source, {})
    entry = {
        "source": source,
        "last_attempt": now_iso(),
        "last_success": now_iso() if ok else prev.get("last_success"),
        "ok": ok,
        "detail": detail,
        "count": count if count is not None else prev.get("count"),
    }
    statuses[source] = entry
    write_json("status.json", statuses)


def reusable_records(filename: str, key_field: str, parser_version: str):
    """Records from a previous run that can be reused without refetching.

    Documents do not change once filed, so there is no reason to download a
    48 MB scan every half hour. Results are keyed by document and reused.

    Bump the module's PARSER_VERSION whenever parsing changes; that invalidates
    everything so improvements apply retroactively instead of only to new
    filings. FORCE_REPARSE=1 in the environment does the same on demand.
    """
    if os.environ.get("FORCE_REPARSE") == "1":
        return {}, set()

    previous = read_json(filename, default=None)
    if not isinstance(previous, list):
        return {}, set()

    grouped: dict[str, list] = {}
    for record in previous:
        if record.get("parser_version") != parser_version:
            return {}, set()  # parser changed: redo everything
        key = record.get(key_field)
        if key:
            grouped.setdefault(str(key), []).append(record)
    return grouped, set(grouped)


def cache_path(*parts: str) -> Path:
    p = CACHE.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_watchlist() -> dict:
    import yaml

    with open(ROOT / "watchlist.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)
