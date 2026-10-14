"""Venue decoders and the one entry point that turns a block transaction into swaps."""
from __future__ import annotations

from ..programs import PUMP, PUMPSWAP, WSOL
from ..tx import TxFacts, account_keys, facts, walk
from . import pump, pumpswap
from .model import EXACT_IN, EXACT_OUT, Swap

INFRA = {"ComputeBudget111111111111111111111111111111", "11111111111111111111111111111111",
         "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
         "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "MemoSq4gqABAXKb96qnH8TuNv1S2bKkJu7TJTNPjjZd"}


def decode_tx(tx: dict, slot: int, tx_index: int, block_time: int | None = None) -> tuple[TxFacts, list[Swap]]:
    """Facts and swaps of one block transaction. A failed transaction yields facts, no swaps."""
    keys = account_keys(tx)
    ixs = list(walk(tx, keys))
    f = facts(tx, ixs)
    tops = [ix.program for ix in ixs if not ix.inner and ix.program not in INFRA]
    f.router = "" if not tops or tops[0] in (PUMP, PUMPSWAP) else tops[0]
    if not f.ok:
        return f, []
    swaps = pump.decode(ixs, f.signature, slot, tx_index) + pumpswap.decode(ixs, f.signature, slot, tx_index)
    budget = spend_budget(tx, f.signer)
    for k, s in enumerate(swaps):
        s.ev_index, s.block_time = k, block_time
        if s.is_buy and s.kind == EXACT_OUT and s.limit is not None and budget is not None:
            s.limit = min(s.limit, budget)    # "no limit" (u64 max) still cannot outspend the wallet
    return f, swaps


def spend_budget(tx: dict, signer: str) -> int | None:
    """Most lamports the signer could pay: SOL plus wrapped-SOL held before the transaction."""
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    if not pre:
        return None
    wsol = sum(int(b["uiTokenAmount"]["amount"]) for b in meta.get("preTokenBalances") or ()
               if b.get("owner") == signer and b.get("mint") == WSOL)
    return int(pre[0]) + wsol


__all__ = ["EXACT_IN", "EXACT_OUT", "Swap", "decode_tx"]
