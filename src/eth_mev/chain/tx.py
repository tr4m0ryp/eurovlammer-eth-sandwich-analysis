"""Ethereum transaction field extraction (the V2 analogue of the Solana account-keys module)."""
from __future__ import annotations

from typing import Any

ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def normalized_tx(tx: dict[str, Any]) -> dict[str, Any]:
    """Return the pending-tx or block-tx fields the decoders consume, with safe defaults.

    Works for both a ``newPendingTransactions`` payload and a block's ``transactions`` entry
    (which may be a full object or a bare hash). Always returns hex ``input`` and lower-cased
    addresses; never trusts that a field is present.
    """
    return {
        "hash": (tx.get("hash") or "").lower(),
        "from": (tx.get("from") or "").lower(),
        "to": (tx.get("to") or "").lower(),
        "value": int(tx.get("value") or 0, 16) if isinstance(tx.get("value"), str) else int(tx.get("value") or 0),
        "gas": int(tx.get("gas") or 0, 16) if isinstance(tx.get("gas"), str) else int(tx.get("gas") or 0),
        "gasPrice": tx.get("gasPrice"),
        "maxFeePerGas": tx.get("maxFeePerGas"),
        "maxPriorityFeePerGas": tx.get("maxPriorityFeePerGas"),
        "nonce": int(tx.get("nonce") or 0, 16) if isinstance(tx.get("nonce"), str) else int(tx.get("nonce") or 0),
        "input": (tx.get("input") or "0x").lower(),
        "transactionIndex": tx.get("transactionIndex", 0) if tx.get("transactionIndex") is not None else 0,
        "chainId": tx.get("chainId"),
        "type": tx.get("type"),
    }


def effective_gas_price(tx: dict[str, Any]) -> int:
    """The gas price a builder pays to include this tx, mixing type-0 and EIP-1559 forms."""
    n = normalized_tx(tx)
    if n["gasPrice"] is not None:
        gp = n["gasPrice"]
        return int(gp, 16) if isinstance(gp, str) else int(gp)
    mf = n["maxFeePerGas"]
    mp = n["maxPriorityFeePerGas"]
    mf = int(mf, 16) if isinstance(mf, str) else (mf or 0)
    mp = int(mp, 16) if isinstance(mp, str) else (mp or 0)
    return int(mf if mf else mp)
