"""SwapRouter / SwapRouter02 calldata decoding, including the nested ``multicall`` form."""
from __future__ import annotations

from typing import Any

from eth_abi import decode as abi_decode

from ...constants import EXACT_INPUT_TYPES, EXACT_OUTPUT_TYPES, SINGLE_TYPES, V3_SELECTORS
from .path import decode_path, selector


def _decode_exact_input(data: bytes) -> dict | None:
    """Decode ``exactInput(bytes path, address recipient, uint256 deadline, uint256 amountIn,
    uint256 amountOutMinimum)`` into the victim's guard + first-hop tokens."""
    try:
        path, _recipient, deadline, amount_in, amount_out_min = abi_decode(EXACT_INPUT_TYPES, data)
    except Exception:
        return None
    hops = decode_path(bytes(path))
    if not hops:
        return None
    tok_in, tok_out, fee = hops[0]
    return {"fn": "exactInput", "token_in": tok_in, "token_out": tok_out, "fee": fee,
            "amount_in": int(amount_in), "amount_out_min": int(amount_out_min),
            "deadline": int(deadline), "n_hops": len(hops)}


def _decode_exact_output(data: bytes) -> dict | None:
    try:
        path, _recipient, deadline, amount_out, amount_in_max = abi_decode(EXACT_OUTPUT_TYPES, data)
    except Exception:
        return None
    hops = decode_path(bytes(path))
    if not hops:
        return None
    tok_in, tok_out, fee = hops[0]
    # exact-output: the victim wants `amount_out`, pays at most `amount_in_max`. Our exposure framing
    # is exact-in (amount_in + amountOutMinimum), so we map amount_in_max -> limit and the last hop's
    # out as the requested amount (approximated here as the first hop; single-hop is exact).
    return {"fn": "exactOutput", "token_in": tok_in, "token_out": tok_out, "fee": fee,
            "amount_in": int(amount_in_max), "amount_out_min": int(amount_out),
            "deadline": int(deadline), "n_hops": len(hops)}


def decode_v3_router(tx: dict[str, Any]) -> dict | None:
    """Decode a V3 victim swap from a SwapRouter02/Old call, including the nested ``multicall`` form.

    Returns the victim's guard triad (``amount_in``, ``amount_out_min``, token pair, fee, deadline)
    or None when the tx does not carry a recognised V3 swap. Handles: exactInputSingle,
    exactOutputSingle, exactInput/exactOutput (bytes path), and the ``multicall`` wrapper.

    **Decode by selector, not by target address.** Many MEV-bot / aggregator routers reuse the
    standard ``exactInputSingle``/``exactInput`` ABI (`0xdfa8ead912…` alone is ~14% of V3 swaps and
    calls `exactInputSingle`), so whitelisting only the Uniswap routers would silently drop them.
    We recognise the swap *selector* and decode defensively regardless of ``to``.
    """
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = selector(data)
    fn = V3_SELECTORS.get(sel)
    if fn is None:
        return None

    # Unwrap a SwapRouter02 multicall: payload = (deadline, bytes[] data) [or bytes[] for the
    # no-deadline form]; the first non-trivial inner call is the actual swap.
    if fn in ("multicall", "multicall_nodl"):
        try:
            if fn == "multicall":
                _deadline, inner_calls = abi_decode(["uint256", "bytes[]"], data[4:])
            else:
                inner_calls = abi_decode(["bytes[]"], data[4:])[0]
        except Exception:
            return None
        for inner in inner_calls:
            if not inner or len(inner) < 4:
                continue
            inner_sel = selector(bytes(inner))
            inner_fn = V3_SELECTORS.get(inner_sel)
            if inner_fn in ("exactInputSingle", "exactOutputSingle", "exactInput"):
                return _decode_inner(inner_fn, bytes(inner)[4:], tx)
        return None

    return _decode_inner(fn, data[4:], tx)


def _decode_inner(fn: str, payload: bytes, tx: dict[str, Any]) -> dict | None:
    if fn in ("exactInputSingle", "exactOutputSingle"):
        try:
            (tok_in, tok_out, fee, _recipient, deadline, amount, amount_limit, _sqrt_limit) = abi_decode(
                SINGLE_TYPES, payload)
        except Exception:
            return None
        amount_in = int(amount)
        amount_out_min = int(amount_limit)
        # for exactOutputSingle the tuple order is (tokenIn, tokenOut, fee, recipient, deadline,
        # amountOut, amountInMaximum, sqrtPriceLimitX96): amount=amountOut, amountLimit=amountInMax.
        if fn == "exactOutputSingle":
            amount_out_min, amount_in = int(amount), int(amount_limit)
        return {"fn": fn, "token_in": tok_in, "token_out": tok_out, "fee": int(fee),
                "amount_in": amount_in, "amount_out_min": amount_out_min, "deadline": int(deadline),
                "user": tx.get("from", ""), "tx_hash": tx.get("hash", ""), "n_hops": 1}
    if fn == "exactInput":
        d = _decode_exact_input(payload)
    elif fn == "exactOutput":
        d = _decode_exact_output(payload)
    else:
        return None
    if d is None:
        return None
    d["user"] = tx.get("from", "")
    d["tx_hash"] = tx.get("hash", "")
    return d
