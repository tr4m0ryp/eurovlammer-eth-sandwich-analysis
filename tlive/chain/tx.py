"""Transaction-level facts shared by every venue decoder: keys, instruction walk, fees, tips.

Works on ``getBlock``/``getTransaction`` records in ``json`` encoding. Every field is read off
the transaction itself; nothing is looked up.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator

import base58

from .programs import COMPUTE_BUDGET, JITO_TIP, SYSTEM

BASE_FEE_PER_SIG = 5000


@dataclass
class Ix:
    """One instruction, top-level or inner, with its accounts resolved to addresses."""
    program: str
    data: bytes
    accounts: list[str]
    top: int            # index of the top-level instruction this sits under
    inner: bool


@dataclass
class TxFacts:
    signature: str
    signer: str
    keys: list[str]
    ok: bool
    fee: int
    priority_fee: int
    cu_used: int | None
    cu_limit: int | None
    cu_price: int | None            # micro-lamports per CU
    jito_tip: int
    transfers: list[tuple[str, int]] = field(default_factory=list)  # signer -> other, lamports
    router: str = ""                # top-level program that carried the swap ("" = direct)
    n_sig: int = 1
    dontfront: bool = False         # carries a ``jitodontfront*`` account: Jito drops bundles that front it


def account_keys(tx: dict) -> list[str]:
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return tx["transaction"]["message"]["accountKeys"] + la.get("writable", []) + la.get("readonly", [])


def walk(tx: dict, keys: list[str]) -> Iterator[Ix]:
    """Top-level instructions in order, each followed by its inner instructions."""
    msg = tx["transaction"]["message"]
    inner: dict[int, list[dict]] = {}
    for g in (tx.get("meta") or {}).get("innerInstructions") or ():
        inner[g["index"]] = g["instructions"]
    for i, ix in enumerate(msg["instructions"]):
        yield _ix(ix, keys, i, False)
        for sub in inner.get(i, ()):
            yield _ix(sub, keys, i, True)


def _ix(ix: dict, keys: list[str], top: int, inner: bool) -> Ix:
    try:
        data = base58.b58decode(ix.get("data") or "")
    except ValueError:
        data = b""
    return Ix(keys[ix["programIdIndex"]], data, [keys[a] for a in ix.get("accounts") or ()], top, inner)


def facts(tx: dict, ixs: list[Ix]) -> TxFacts:
    meta = tx.get("meta") or {}
    keys = account_keys(tx)
    sigs = tx["transaction"].get("signatures") or [""]
    cu_limit = cu_price = None
    jito = 0
    transfers: list[tuple[str, int]] = []
    signer = keys[0]
    for ix in ixs:
        if ix.program == COMPUTE_BUDGET and ix.data:
            if ix.data[0] == 2 and len(ix.data) >= 5:
                cu_limit = struct.unpack_from("<I", ix.data, 1)[0]
            elif ix.data[0] == 3 and len(ix.data) >= 9:
                cu_price = struct.unpack_from("<Q", ix.data, 1)[0]
        elif ix.program == SYSTEM and len(ix.data) >= 12 and ix.data[:4] == b"\x02\x00\x00\x00" \
                and len(ix.accounts) >= 2 and ix.accounts[0] == signer:
            lamports = struct.unpack_from("<Q", ix.data, 4)[0]
            if ix.accounts[1] in JITO_TIP:
                jito += lamports
            else:
                transfers.append((ix.accounts[1], lamports))
    fee = meta.get("fee") or 0
    return TxFacts(signature=sigs[0], signer=signer, keys=keys, ok=meta.get("err") is None, fee=fee,
                   priority_fee=max(fee - BASE_FEE_PER_SIG * len(sigs), 0),
                   cu_used=meta.get("computeUnitsConsumed"), cu_limit=cu_limit, cu_price=cu_price,
                   jito_tip=jito, transfers=transfers, n_sig=len(sigs),
                   dontfront=any(k.startswith("jitodontfront") for k in keys))
