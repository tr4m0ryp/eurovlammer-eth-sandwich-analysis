"""Uniswap UniversalRouter ``execute`` command decoding.

The modern default router wraps swaps in ``execute(bytes commands, bytes[] inputs[, uint256 deadline])``
where each ``commands`` byte is a command code and ``inputs[i]`` its ABI-encoded argument. The V3 swap
commands carry the victim's guard directly:
  V3_SWAP_EXACT_IN  (0x00): (address recipient, uint256 amountIn, uint256 amountOutMin, bytes path, bool payerIsUser)
  V3_SWAP_EXACT_OUT (0x01): (address recipient, uint256 amountOut, uint256 amountInMax, bytes path, bool payerIsUser)
"""
from __future__ import annotations

from typing import Any

from eth_abi import decode as abi_decode

from .path import decode_path, selector

UR_EXECUTE_SELECTORS = {"0x24856bc3": "execute(bytes,bytes[])", "0x3593564c": "execute(bytes,bytes[],uint256)"}
UR_V3_SWAP_EXACT_IN = 0x00
UR_V3_SWAP_EXACT_OUT = 0x01
UR_SWAP_INPUT_TYPES = ["address", "uint256", "uint256", "bytes", "bool"]


def decode_universal_router(tx: dict[str, Any]) -> dict | None:
    """Decode a victim swap from a Uniswap UniversalRouter ``execute`` call.

    Walks the ``commands`` byte string for the first V3 swap command (V3_SWAP_EXACT_IN/OUT) and
    returns its guard (``amount_out_min``) + first-hop token pair + fee. Returns None when the tx
    is not a UniversalRouter execute, or has no V3 swap command, or any field fails to decode."""
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = selector(data)
    sig = UR_EXECUTE_SELECTORS.get(sel)
    if sig is None:
        return None
    try:
        if sig == "execute(bytes,bytes[])":
            commands, inputs = abi_decode(["bytes", "bytes[]"], data[4:])
        else:
            commands, inputs, _deadline = abi_decode(["bytes", "bytes[]", "uint256"], data[4:])
    except Exception:
        return None
    commands_b = bytes(commands)
    inputs_l = list(inputs)
    for i, cmd in enumerate(commands_b):
        if i >= len(inputs_l):
            break
        if cmd == UR_V3_SWAP_EXACT_IN or cmd == UR_V3_SWAP_EXACT_OUT:
            try:
                (recipient, amount, amount_limit, path, _payer) = abi_decode(
                    UR_SWAP_INPUT_TYPES, bytes(inputs_l[i]))
            except Exception:
                continue
            hops = decode_path(bytes(path))
            if not hops:
                continue
            tok_in, tok_out, fee = hops[0]
            if cmd == UR_V3_SWAP_EXACT_IN:
                amount_in, amount_out_min = int(amount), int(amount_limit)
            else:
                # exact out: victim wants `amount` out, pays <= `amount_limit` in
                amount_out_min, amount_in = int(amount), int(amount_limit)
            return {"fn": "universal_router_execute", "token_in": tok_in, "token_out": tok_out,
                    "fee": fee, "amount_in": amount_in, "amount_out_min": amount_out_min,
                    "deadline": None, "n_hops": len(hops), "user": tx.get("from", ""),
                    "tx_hash": tx.get("hash", "")}
    return None
