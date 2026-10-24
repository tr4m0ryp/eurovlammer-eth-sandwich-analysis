"""Fallback decoding for undocumented router wrappers: find an embedded standard swap call."""
from __future__ import annotations

from typing import Any

from eth_abi import decode as abi_decode

from ..constants import SINGLE_TYPES

# Selectors of standard swap calls that unknown wrappers commonly embed, with the ABI type list to
# decode the payload immediately following the selector.
_EMBEDDED_SWAPS = {
    "0x414bf389": ("exactInputSingle", SINGLE_TYPES),            # (tokenIn,tokenOut,fee,recipient,deadline,amountIn,amountOutMinimum,sqrtPriceLimit)
    "0xdb3e2198": ("exactOutputSingle", SINGLE_TYPES),           # same, amount=amountOut, limit=amountInMax
    "0x38ed1739": ("v2_exactTokensForTokens", ["uint256", "uint256", "address[]", "address", "uint256"]),
    "0x18cbafe5": ("v2_exactTokensForETH", ["uint256", "uint256", "address[]", "address", "uint256"]),
    "0x7ff36ab5": ("v2_exactETHForTokens", ["uint256", "uint256", "address[]", "address", "uint256"]),
}


def decode_embedded_swap(tx: dict[str, Any]) -> dict | None:
    """Generic fallback for *undocumented* router wrappers: scan the raw calldata for an embedded
    standard swap selector (``exactInputSingle``/``exactOutputSingle``/V2 ``swapExact*``) at any
    offset, and decode the ABI payload that follows it. Recovers the victim's guard from opaque
    bot routers whose only decodable part is the inner Uniswap call they make.

    Scans every byte offset for a 4-byte selector match; a false-positive is rejected by the ABI
    decode (which returns None on malformed payload), so the first clean decode wins.
    """
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    for off in range(0, max(0, len(data) - 4)):
        sel = "0x" + data[off:off + 4].hex()
        spec = _EMBEDDED_SWAPS.get(sel)
        if spec is None:
            continue
        fn, types = spec
        payload = data[off + 4:]
        if fn in ("exactInputSingle", "exactOutputSingle"):
            if len(payload) < 256:
                continue
            try:
                (tok_in, tok_out, fee, _recipient, deadline, amount, amount_limit, _sqrt) = abi_decode(
                    types, payload[:256])
            except Exception:
                continue
            if fn == "exactOutputSingle":
                amount_out_min, amount_in = int(amount), int(amount_limit)
            else:
                amount_in, amount_out_min = int(amount), int(amount_limit)
            return {"fn": f"embedded_{fn}", "token_in": tok_in, "token_out": tok_out,
                    "fee": int(fee), "amount_in": amount_in, "amount_out_min": amount_out_min,
                    "deadline": int(deadline), "n_hops": 1, "user": tx.get("from", ""),
                    "tx_hash": tx.get("hash", "")}
        else:
            # V2 swapExactTokensForTokens/ETH: (amountIn, amountOutMin, path, to, deadline).
            # ``path`` is a dynamic address[] — decode from the full remaining payload and let
            # abi_decode consume exactly what it needs (try/except rejects false-positive matches).
            if len(payload) < 160:
                continue
            try:
                amount_in, amount_out_min, path, _to, deadline = abi_decode(types, payload)
            except Exception:
                continue
            path = list(path)
            if len(path) < 2:
                continue
            return {"fn": f"embedded_{fn}", "token_in": path[0], "token_out": path[-1],
                    "fee": 0, "amount_in": int(amount_in), "amount_out_min": int(amount_out_min),
                    "deadline": int(deadline), "n_hops": len(path) - 1, "user": tx.get("from", ""),
                    "tx_hash": tx.get("hash", "")}
    return None
