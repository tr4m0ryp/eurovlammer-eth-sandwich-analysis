"""PumpSwap AMM swaps: instruction args paired with the program's Buy/SellEvent (emit_cpi copy).

Instruction args (after the 8-byte discriminator), first account = pool, fifth = quote mint:
    buy                              base_amount_out, max_quote_amount_in   -> exact-out buy
    buy_exact_quote_in / *_v2        spendable_quote_in, min_base_amount_out -> exact-in buy
    sell / sell_v2                   base_amount_in, min_quote_amount_out   -> exact-in sell

Event body offsets (verified in trenches ``src.ingest.pumpswap.events`` against token balances):
base amount 8, pool base/quote reserves PRE 40/48, sell output 56, lp bps/fee 64/72, protocol bps
80, buy pool intake 96, pool 112, user 144, coin-creator bps 336. The pool prices against ``balance + virtual
quote`` since InitBoost, so the effective quote reserve is recovered from the swap itself:
``Q = q (B - b) / b`` on a buy, ``Q = q (B + b) / b`` on a sell.
"""
from __future__ import annotations

import struct

from ..programs import EV, EVENT_IX_TAG, PUMPSWAP, WSOL, disc
from ..tx import Ix
from .model import EXACT_IN, EXACT_OUT, Swap
from .pump import b58

_IXS = {
    disc("buy"): ("buy", True, EXACT_OUT),
    disc("buy_exact_quote_in"): ("buy_exact_quote_in", True, EXACT_IN),
    disc("buy_exact_quote_in_v2"): ("buy_exact_quote_in_v2", True, EXACT_IN),
    disc("sell"): ("sell", False, EXACT_IN), disc("sell_v2"): ("sell_v2", False, EXACT_IN),
}
_EVS = {EV["BuyEvent"]: True, EV["SellEvent"]: False}


def _u64(b: bytes, o: int) -> int:
    return struct.unpack_from("<Q", b, o)[0]


def decode(ixs: list[Ix], signature: str, slot: int, tx_index: int) -> list[Swap]:
    out: list[Swap] = []
    pending: list[tuple] = []
    for ix in ixs:
        if ix.program != PUMPSWAP or len(ix.data) < 8:
            continue
        head = ix.data[:8]
        if head in _IXS and len(ix.data) >= 24:
            name, is_buy, kind = _IXS[head]
            a, lim = struct.unpack_from("<QQ", ix.data, 8)
            quote = ix.accounts[4] if len(ix.accounts) > 4 else ""
            base = ix.accounts[3] if len(ix.accounts) > 3 else ""
            pending.append((name, is_buy, kind, a, lim, quote, base))
        elif head == EVENT_IX_TAG and ix.data[8:16] in _EVS and len(ix.data) >= 16 + 352:
            is_buy = _EVS[ix.data[8:16]]
            body = ix.data[16:]
            arg = pending.pop(0) if pending else None
            if arg is None or arg[5] != WSOL:
                continue                               # non-SOL quote, or unpaired
            b, big_b = _u64(body, 8), _u64(body, 40)
            # curve-side quote: a buy's pool intake minus the lp fee (offset 56 holds the GROSS
            # spend on exact-in buys, the net on exact-out buys); a sell's raw output at 56.
            q = _u64(body, 96) - _u64(body, 72) if is_buy else _u64(body, 56)
            if b == 0 or q == 0 or (is_buy and b >= big_b):
                continue
            q_eff = q * (big_b - b) / b if is_buy else q * (big_b + b) / b
            fee_bps = _u64(body, 64) + _u64(body, 80) + _u64(body, 336)
            name, _, kind, amount, limit = arg[:5] if arg[1] == is_buy else ("?", is_buy, EXACT_IN, None, None)
            out.append(Swap(signature, slot, tx_index, len(out), "pumpswap", b58(body[112:144]), arg[6],
                            b58(body[144:176]), is_buy, q, b, q_eff, float(big_b), float(fee_bps),
                            kind, amount, limit, name))
    return out
