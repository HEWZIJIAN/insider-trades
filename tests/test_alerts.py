"""Alert threshold tests.

With every House member tracked, the threshold is the only thing standing
between you and a notification for every $1,001 disclosure. These tests pin
which trades get through.

The key decision: filings disclose a BAND, not a figure, so the test is on the
BOTTOM of the band. "$50,001 - $100,000" passes a $50,001 threshold because it
is certainly at least that. "$15,001 - $50,000" does not, even though it could
be $50,000 - erring towards silence there would be wrong, but so would
alerting on every band that merely overlaps the threshold.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from alerts import passes_amount_threshold  # noqa: E402

MIN = 50001


def _passes(trade, minimum=MIN, unreadable=False):
    ok, _ = passes_amount_threshold(trade, minimum, unreadable)
    return ok


# ---------------------------------------------------------------------------
# Range-based filings (House PTRs, OGE 278-T).
# ---------------------------------------------------------------------------
def test_bands_at_or_above_the_threshold_alert():
    for low in (50001, 100001, 250001, 500001, 1000001):
        assert _passes({"parsed": True, "amount_min": low}), low


def test_bands_below_the_threshold_are_silent():
    for low in (1001, 15001):
        assert not _passes({"parsed": True, "amount_min": low}), low


def test_the_band_just_under_is_silent():
    # "$15,001 - $50,000" tops out a dollar short. It must not alert.
    assert not _passes({"parsed": True, "amount_min": 15001, "amount_max": 50000})


# ---------------------------------------------------------------------------
# Form 4 reports an exact value rather than a band.
# ---------------------------------------------------------------------------
def test_form4_uses_its_exact_value():
    assert _passes({"parsed": True, "value_usd": 1_003_857.0})
    assert not _passes({"parsed": True, "value_usd": 10_465.0})


# ---------------------------------------------------------------------------
# On-chain records have no dollar value at all.
# ---------------------------------------------------------------------------
def test_crypto_swap_always_alerts():
    """A deliberate swap is rare and worth knowing about, and we cannot price it."""
    assert _passes({"parsed": True, "source": "crypto", "is_swap": True})


def test_airdrops_never_alert():
    assert not _passes({"parsed": True, "source": "crypto", "likely_airdrop": True})
    # Even a swap flag must not rescue something already judged spam.
    assert not _passes({
        "parsed": True, "source": "crypto", "likely_airdrop": True, "is_swap": False,
    })


def test_plain_transfers_never_alert():
    assert not _passes({"parsed": True, "source": "crypto", "is_swap": False})


# ---------------------------------------------------------------------------
# Filings we could not read.
# ---------------------------------------------------------------------------
def test_unreadable_filings_follow_their_setting():
    doc = {"parsed": False}
    assert not _passes(doc, unreadable=False)
    assert _passes(doc, unreadable=True)


def test_no_threshold_lets_everything_through():
    assert _passes({"parsed": True, "amount_min": 1001}, minimum=0)
    assert _passes({"parsed": False}, minimum=0)


def test_reason_is_always_given():
    """The run reports what it held back, so every path must explain itself."""
    for trade in (
        {"parsed": True, "amount_min": 1001},
        {"parsed": True, "value_usd": 5.0},
        {"parsed": True, "source": "crypto", "likely_airdrop": True},
        {"parsed": False},
        {"parsed": True},
    ):
        _, reason = passes_amount_threshold(trade, MIN, False)
        assert reason and isinstance(reason, str), trade


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
