"""Reverse-engineered decoders for private/undocumented swap routers.

These routers have no published ABI (they return ``UNKNOWN`` from every signature DB), but their
calldata and on-chain call traces reveal the structure. Each decoder extracts the victim's slippage
guard so the exposure math can score these otherwise-invisible swaps.

Reverse-engineering method (per router): capture a sample tx → ``debug_traceTransaction`` (callTracer)
to see the internal V3-pool ``swap`` call → correlate the pool-swap args with the router calldata words
→ recover the guard field position. The result is pinned by unit tests against the traced layout.
"""
from __future__ import annotations

from typing import Any

# 0x51c72848c68a965f66fa7a88855f9f7784502a7f — a direct V3-pool-swap router. Both entrypoints share
# one layout: (address, address pool, uint256 amountIn, uint256 minAmountOut, bool zeroForOne,
#              [uint256 deadline], bytes tokenData). The guard is minAmountOut (word[3]).
ROUTER_51C7 = "0x51c72848c68a965f66fa7a88855f9f7784502a7f"
_51C7_SWAP_SELECTORS = {
    "0x771d503f": ("51c7_swap", False),   # no deadline
    "0x122067ed": ("51c7_swap", True),    # with deadline
}


def decode_51c7(tx: dict[str, Any]) -> dict | None:
    """Decode a ``0x51c72848…`` direct pool-swap victim. Returns the guard triad + pool + direction."""
    from eth_abi import decode as abi_decode
    to = (tx.get("to") or "").lower()
    if to != ROUTER_51C7:
        return None
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = "0x" + data[:4].hex()
    spec = _51C7_SWAP_SELECTORS.get(sel)
    if spec is None:
        return None
    fn, has_deadline = spec
    types = ["address", "address", "uint256", "uint256", "bool"]
    if has_deadline:
        types.append("uint256")
    types.append("bytes")
    try:
        vals = abi_decode(types, data[4:])
    except Exception:
        return None
    _arg0, pool, amount_in, amount_out_min, zero_for_one = vals[:5]
    deadline = vals[5] if has_deadline and len(vals) > 5 else None
    return {
        "fn": fn,
        "token_in": "", "token_out": "",   # pool token order is resolved by the caller's pool read
        "pool": pool,
        "fee": 0,
        "amount_in": int(amount_in),
        "amount_out_min": int(amount_out_min),
        "zero_for_one": bool(zero_for_one),
        "deadline": int(deadline) if deadline is not None else None,
        "n_hops": 1,
        "user": tx.get("from", ""),
        "tx_hash": tx.get("hash", ""),
    }


# 0x278d858f05b94576c1e6f73285886876ff6ef8d2 — a proxy (DELEGATECALL → 0x617e169f…) that does a direct
# V3-pool swap. Layout: (uint256 amountIn, address token, uint256 amountOutMin, bool zeroForOne,
#                       address pool, uint256 deadline, uint256 flag). Guard = amountOutMin (word[2]).
ROUTER_278D = "0x278d858f05b94576c1e6f73285886876ff6ef8d2"
_278D_SELECTORS = {"0x42e3dcf3": "278d_swap"}


def decode_278d(tx: dict[str, Any]) -> dict | None:
    from eth_abi import decode as abi_decode
    to = (tx.get("to") or "").lower()
    if to != ROUTER_278D:
        return None
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = "0x" + data[:4].hex()
    if sel not in _278D_SELECTORS:
        return None
    types = ["uint256", "address", "uint256", "bool", "address", "uint256", "uint256"]
    try:
        amount_in, token, amount_out_min, zero_for_one, pool, deadline, _flag = abi_decode(types, data[4:])
    except Exception:
        return None
    return {
        "fn": "278d_swap",
        "token_in": "", "token_out": token,   # pool token order resolved by the caller's pool read
        "pool": pool,
        "fee": 0,
        "amount_in": int(amount_in),
        "amount_out_min": int(amount_out_min),
        "zero_for_one": bool(zero_for_one),
        "deadline": int(deadline),
        "n_hops": 1,
        "user": tx.get("from", ""),
        "tx_hash": tx.get("hash", ""),
    }


# Registry of every private-router decoder, tried in order.
PRIVATE_ROUTER_DECODERS = [decode_51c7, decode_278d]


def decode_private_router(tx: dict[str, Any]) -> dict | None:
    for dec in PRIVATE_ROUTER_DECODERS:
        d = dec(tx)
        if d is not None:
            return d
    return None
