"""pump.fun bonding-curve swaps: instruction args paired with the program's TradeEvent.

Instruction layouts (8-byte anchor discriminator, then little-endian u64 args), checked against
the paired event on mainnet blocks of 2026-10-14:
    buy / buy_v3                     amount (tokens out), max_sol_cost   -> exact-out buy
    buy_exact_sol_in / *_quote_in_*  spendable_sol_in, min_tokens_out   -> exact-in buy
    sell / sell_v2                   amount (tokens in), min_sol_output -> exact-in sell

TradeEvent (blob = discriminator + body): sol at 40, tok at 48, is_buy at 56, user at 57,
virtual reserves POST-trade at 97/105, protocol fee bps/fee at 161/169. The creator leg moves
between event variants, so it is found by its own identity ``fee == ceil(sol * bps / 1e4)``
past offset 177 (trenches F13 showed the fixed offset reads zero on 70% of variants).
Events with ``sol == 0`` are non-SOL-quoted coins and are skipped: they cannot be valued in SOL.
"""
from __future__ import annotations

import math
import struct

from ..programs import EV, EVENT_IX_TAG, PUMP, disc
from ..tx import Ix
from .model import EXACT_IN, EXACT_OUT, Swap

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_IXS = {
    disc("buy"): ("buy", True, EXACT_OUT), disc("buy_v3"): ("buy_v3", True, EXACT_OUT),
    disc("buy_exact_sol_in"): ("buy_exact_sol_in", True, EXACT_IN),
    disc("buy_exact_quote_in_v2"): ("buy_exact_quote_in_v2", True, EXACT_IN),
    disc("buy_exact_quote_in_v3"): ("buy_exact_quote_in_v3", True, EXACT_IN),
    disc("sell"): ("sell", False, EXACT_IN), disc("sell_v2"): ("sell_v2", False, EXACT_IN),
}
_EV_BODY = struct.Struct("<32sQQ?32sqQQ")


def b58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = []
    while n:
        n, r = divmod(n, 58)
        out.append(B58[r])
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + "".join(reversed(out))


def _creator_bps(blob: bytes, sol: int) -> int:
    for o in range(177, len(blob) - 15):
        bps, fee = struct.unpack_from("<QQ", blob, o)
        if 0 < bps <= 1000 and fee and fee == math.ceil(sol * bps / 1e4):
            return bps
    return 0


def decode(ixs: list[Ix], signature: str, slot: int, tx_index: int) -> list[Swap]:
    """Swaps in one successful transaction, each instruction paired with the next event."""
    out: list[Swap] = []
    pending: list[tuple[str, bool, str, int, int]] = []
    for ix in ixs:
        if ix.program != PUMP or len(ix.data) < 8:
            continue
        head = ix.data[:8]
        if head in _IXS and len(ix.data) >= 24:
            name, is_buy, kind = _IXS[head]
            a, lim = struct.unpack_from("<QQ", ix.data, 8)
            pending.append((name, is_buy, kind, a, lim))
        elif head == EVENT_IX_TAG and ix.data[8:16] == EV["TradeEvent"] and len(ix.data) >= 8 + 177:
            blob = ix.data[8:]
            mint, sol, tok, is_buy, user, _ts, v_sol, v_tok = _EV_BODY.unpack_from(blob, 8)
            arg = pending.pop(0) if pending else None
            if sol == 0:
                continue
            proto_bps = struct.unpack_from("<Q", blob, 161)[0]
            fee_bps = proto_bps + _creator_bps(blob, sol)
            name, kind, amount, limit = ("?", EXACT_IN, None, None)
            if arg is not None and arg[1] == bool(is_buy):
                name, _, kind, amount, limit = arg
            x_pre = v_sol - sol if is_buy else v_sol + sol
            y_pre = v_tok + tok if is_buy else v_tok - tok
            out.append(Swap(signature, slot, tx_index, len(out), "pump", b58(mint), b58(mint),
                            b58(user), bool(is_buy), sol, tok, float(x_pre), float(y_pre),
                            float(fee_bps), kind, amount, limit, name))
    return out
