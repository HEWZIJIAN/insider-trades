"""On-chain activity for wallets named in the watchlist.

Uses Blockscout, which needs no API key. Etherscan would need one; Blockscout
does not, so this stays free with nothing to sign up for.

Two honesty rules specific to this source:

  1. An address is only watched if YOU supplied it, or a named public source
     labelled it. Every record carries that label source and shows it in the
     app, so a wallet is never silently attributed to a person on rumour.

  2. A token transfer is not a trade. Moving coins between your own wallets,
     receiving an airdrop and buying on a DEX all look similar on-chain. Only
     transfers whose contract method is a swap are treated as trades; the rest
     are reported as "received"/"sent" and excluded from the copy-check.
"""
from __future__ import annotations

import sys

from common import (
    delay_days,
    get,
    load_watchlist,
    now_iso,
    slug,
    update_status,
    write_json,
)

SOURCE_ID = "crypto"
PARSER_VERSION = "2026-09-22.1"
SOURCE_LABEL = "On-chain (Blockscout)"

# Blockscout instances that are free and keyless.
CHAINS = {
    "ethereum": {"api": "https://eth.blockscout.com", "explorer": "https://etherscan.io"},
    "base": {"api": "https://base.blockscout.com", "explorer": "https://basescan.org"},
    "polygon": {"api": "https://polygon.blockscout.com", "explorer": "https://polygonscan.com"},
    "optimism": {"api": "https://optimism.blockscout.com", "explorer": "https://optimistic.etherscan.io"},
}

MAX_TRANSFERS_PER_WALLET = 200
SWAP_HINTS = ("swap", "exactinput", "exactoutput", "multicall", "trade")

# Blockscout returns a decoded method name when it knows the ABI and the raw
# 4-byte selector when it does not. Decode the common ones so the app shows
# something readable. An unknown selector stays unknown and is treated as a
# plain movement, never as a trade.
SELECTORS = {
    "0xa9059cbb": "transfer",
    "0x23b872dd": "transferFrom",
    "0x095ea7b3": "approve",
    "0x38ed1739": "swapExactTokensForTokens",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x5ae401dc": "multicall",
    "0x3593564c": "execute",
    "0x04e45aaf": "exactInputSingle",
    "0xb6f9de95": "swapExactETHForTokensSupportingFeeOnTransferTokens",
}


def decode_method(raw: str | None) -> str | None:
    if not raw:
        return None
    return SELECTORS.get(raw.lower(), raw)


def _amount(total: dict) -> float | None:
    try:
        value = int(total.get("value"))
        decimals = int(total.get("decimals") or 0)
    except (TypeError, ValueError):
        return None
    return value / (10 ** decimals)


def fetch_transfers(wallet: dict) -> list[dict]:
    chain = CHAINS.get((wallet.get("chain") or "ethereum").lower())
    if not chain:
        raise ValueError(f"unsupported chain: {wallet.get('chain')}")

    address = wallet["address"]
    url = f"{chain['api']}/api/v2/addresses/{address}/token-transfers?type=ERC-20"
    items = (get(url).json() or {}).get("items", [])
    return items[:MAX_TRANSFERS_PER_WALLET]


def to_record(item: dict, wallet: dict) -> dict:
    chain_key = (wallet.get("chain") or "ethereum").lower()
    chain = CHAINS[chain_key]
    address = (wallet["address"] or "").lower()

    sender = ((item.get("from") or {}).get("hash") or "").lower()
    recipient = ((item.get("to") or {}).get("hash") or "").lower()
    incoming = recipient == address

    token = item.get("token") or {}
    method = decode_method(item.get("method"))
    is_swap = any(hint in (method or "").lower() for hint in SWAP_HINTS)

    # Only a swap is a trade. Everything else is a movement of tokens.
    if is_swap:
        action = "buy" if incoming else "sell"
    else:
        action = "received" if incoming else "sent"

    timestamp = (item.get("timestamp") or "")[:10] or None
    tx_hash = item.get("transaction_hash") or item.get("tx_hash") or ""

    return {
        "id": f"chain-{chain_key}-{tx_hash}-{item.get('log_index')}",
        "source": SOURCE_ID,
        "source_label": f"{SOURCE_LABEL} - {chain_key}",
        "asset_class": "crypto",
        "person": wallet.get("name") or wallet["address"],
        "person_id": slug(wallet.get("name") or wallet["address"]),
        "role": f"Wallet on {chain_key}",
        "wallet_address": wallet["address"],
        # Who says this wallet belongs to this person. Always shown in the app.
        "label_source": wallet.get("label_source"),
        "label_source_url": wallet.get("label_source_url"),
        "asset_name": token.get("name") or token.get("symbol"),
        "ticker": token.get("symbol"),
        "action": action,
        "on_chain_method": method,
        "is_swap": is_swap,
        "quantity": _amount(item.get("total") or {}),
        "counterparty": recipient if not incoming else sender,
        # On-chain activity is public the moment it is mined: no disclosure lag.
        "trade_date": timestamp,
        "disclosure_date": timestamp,
        "delay_days": delay_days(timestamp, timestamp),
        "block_number": item.get("block_number"),
        "doc_id": tx_hash,
        "source_url": f"{chain['explorer']}/tx/{tx_hash}",
        "parsed": True,
        "parser_version": PARSER_VERSION,
    }


def run() -> list[dict]:
    cfg = load_watchlist()
    wallets = cfg.get("crypto_wallets") or []

    if not wallets:
        write_json("crypto.json", [])
        update_status(
            SOURCE_ID,
            ok=True,
            detail=(
                "no wallets configured - add addresses to watchlist.yml, each "
                "with a label_source naming who attributes it"
            ),
            count=0,
        )
        return []

    records: list[dict] = []
    errors: list[str] = []

    for wallet in wallets:
        if not wallet.get("address"):
            errors.append("a wallet entry has no address")
            continue
        # The brief's rule: no address without a stated source for the label.
        if not wallet.get("label_source"):
            errors.append(f"{wallet['address'][:12]}... has no label_source; skipped")
            continue
        try:
            for item in fetch_transfers(wallet):
                records.append(to_record(item, wallet))
        except Exception as exc:
            errors.append(f"{wallet.get('name') or wallet['address'][:12]}: {exc}")

    records.sort(key=lambda r: (r.get("trade_date") or "", r.get("id")), reverse=True)
    write_json("crypto.json", records)

    swaps = sum(1 for r in records if r.get("is_swap"))
    update_status(
        SOURCE_ID,
        ok=not errors,
        detail=(
            f"{len(wallets)} wallets, {len(records)} transfers ({swaps} swaps "
            f"treated as trades)" + (f"; issues: {'; '.join(errors)}" if errors else "")
        ),
        count=len(records),
    )
    return records


if __name__ == "__main__":
    out = run()
    if not out:
        print("No wallets configured (or none with a label_source). Wrote an empty crypto.json.")
    else:
        swaps = sum(1 for r in out if r.get("is_swap"))
        print(f"{len(out)} transfers, {swaps} swaps treated as trades")
    sys.exit(0)
