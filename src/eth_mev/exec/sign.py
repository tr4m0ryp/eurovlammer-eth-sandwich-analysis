"""Transaction signing (eth-account), gated behind funded keys.

Never auto-loads a key; the operator must supply it explicitly (or via env, which is documented and
never logged/committed). Key material is held in memory only.
"""
from __future__ import annotations

import os

from eth_account import Account
from eth_account.signers.local import LocalAccount

MISSING = RuntimeError("no bot key: set ETH_BOT_KEY (hex privkey) or pass it explicitly; "
                       "live sends are disabled without a funded, dedicated hot wallet")


def load_account(privkey: str | None = None) -> LocalAccount:
    """Return a LocalAccount from an explicit key or ``ETH_BOT_KEY``. Refuses to log the key."""
    key = privkey or os.environ.get("ETH_BOT_KEY") or os.environ.get("ETH_BOT_PRIVKEY")
    if not key or key.strip() == "":
        raise MISSING
    key = key.strip()
    if key.startswith("0x"):
        key = key[2:]
    try:
        return Account.from_key(bytes.fromhex(key))
    except Exception as e:
        raise RuntimeError(f"invalid bot key (not loaded): {e.__class__.__name__}") from e


def sign_tx(account: LocalAccount, tx: dict) -> bytes:
    """Sign a raw transaction dict with the account; returns the raw signed-tx bytes (rlp, .hex())."""
    signed = account.sign_transaction(tx)
    return signed.raw_transaction
