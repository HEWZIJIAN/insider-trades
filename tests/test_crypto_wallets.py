"""On-chain parsing tests.

Two things these defend, both of which would otherwise mislead badly:

  * A token transfer is not a trade. Only a swap becomes a buy or a sell.
  * Anyone can send any token to any address. A famous wallet is mostly
    unsolicited spam - Donald Trump's labelled wallet was 46 of 50 - and
    reporting that as "acquired X" would be worse than showing nothing.

The airdrop check silently returned False for a while because Blockscout calls
the field `address_hash` and the code asked for `address`. Hence the explicit
field-name test.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sources"))

from crypto_wallets import (  # noqa: E402
    decode_method,
    to_record,
    tokens_ever_sent,
)

WALLET = {
    "address": "0xAAAA000000000000000000000000000000000001",
    "name": "Test Wallet",
    "chain": "ethereum",
    "label_source": "Etherscan public name tag",
    "label_source_url": "https://etherscan.io/address/0xAAAA",
}
ME = WALLET["address"].lower()
OTHER = "0xbbbb000000000000000000000000000000000002"


def _transfer(*, symbol, contract, frm, to, method, value="1000", decimals="0"):
    return {
        "transaction_hash": "0xdead", "log_index": 1,
        "timestamp": "2026-09-01T00:00:00.000000Z",
        "block_number": 1,
        "from": {"hash": frm}, "to": {"hash": to},
        "method": method,
        "token": {"symbol": symbol, "name": symbol, "address_hash": contract,
                  "decimals": decimals},
        "total": {"value": value, "decimals": decimals},
    }


# ---------------------------------------------------------------------------
def test_plain_incoming_transfer_is_not_a_buy():
    item = _transfer(symbol="FAFO", contract="0xc1", frm=OTHER, to=ME,
                     method="0xa9059cbb")
    rec = to_record(item, WALLET, set())
    assert rec["action"] == "received", "a transfer must never be called a buy"
    assert rec["is_swap"] is False


def test_swap_is_a_real_trade():
    item = _transfer(symbol="ARTEMIS", contract="0xc2", frm=OTHER, to=ME,
                     method="0x7ff36ab5")   # swapExactETHForTokens
    rec = to_record(item, WALLET, set())
    assert rec["is_swap"] is True
    assert rec["action"] == "buy"
    # A swap is a deliberate purchase, so it is never airdrop spam.
    assert rec["likely_airdrop"] is False


def test_outgoing_transfer_is_sent():
    item = _transfer(symbol="USD1", contract="0xc3", frm=ME, to=OTHER,
                     method="0xa9059cbb")
    rec = to_record(item, WALLET, {"0xc3"})
    assert rec["action"] == "sent"
    assert rec["likely_airdrop"] is False


# ---------------------------------------------------------------------------
# Airdrop detection.
# ---------------------------------------------------------------------------
def test_unsolicited_token_is_flagged_as_airdrop():
    item = _transfer(symbol="pwease", contract="0xspam", frm=OTHER, to=ME,
                     method="0xa9059cbb")
    rec = to_record(item, WALLET, sent_tokens=set())   # never sent this token
    assert rec["likely_airdrop"] is True


def test_token_the_wallet_actually_uses_is_not_airdrop():
    item = _transfer(symbol="USD1", contract="0xc3", frm=OTHER, to=ME,
                     method="0xa9059cbb")
    rec = to_record(item, WALLET, sent_tokens={"0xc3"})  # it has sent USD1 before
    assert rec["likely_airdrop"] is False


def test_tokens_ever_sent_reads_the_right_field():
    """Regression: Blockscout calls it address_hash, not address."""
    items = [
        _transfer(symbol="USD1", contract="0xC3", frm=ME, to=OTHER, method="0xa9059cbb"),
        _transfer(symbol="SPAM", contract="0xC9", frm=OTHER, to=ME, method="0xa9059cbb"),
    ]
    sent = tokens_ever_sent(items, WALLET["address"])
    assert sent == {"0xc3"}, f"expected the sent token only, got {sent}"


# ---------------------------------------------------------------------------
def test_label_source_is_carried_onto_every_record():
    item = _transfer(symbol="USD1", contract="0xc3", frm=OTHER, to=ME,
                     method="0xa9059cbb")
    rec = to_record(item, WALLET, set())
    assert rec["label_source"] == "Etherscan public name tag"
    assert rec["label_source_url"].startswith("https://etherscan.io/")


def test_method_selectors_are_decoded():
    assert decode_method("0xa9059cbb") == "transfer"
    assert decode_method("0x38ed1739") == "swapExactTokensForTokens"
    assert decode_method("0xdeadbeef") == "0xdeadbeef"   # unknown stays raw
    assert decode_method(None) is None


def test_quantity_respects_decimals():
    item = _transfer(symbol="USD1", contract="0xc3", frm=OTHER, to=ME,
                     method="0xa9059cbb", value="2888200000000000000000000",
                     decimals="18")
    rec = to_record(item, WALLET, set())
    assert rec["quantity"] == 2888200.0


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
