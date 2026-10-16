"""Uniswap V3 swap decoding (calldata + ``Swap`` event logs) and the concentrated-liquidity record.

A V3 pool has liquidity concentrated in discrete tick ranges; the ``Swap`` event carries the signed
amounts (``amount0``/``amount1``, negative = in, positive = out), the post-swap ``sqrtPriceX96``,
``liquidity`` and ``tick``. The victim's slippage guard is ``amountOutMinimum`` in the router calldata
(SwapRouter02 ``exactInputSingle``), the V3 analogue of V2's ``amountOutMin``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .v3_constants import (V3_SELECTORS, V3_SWAP_EVENT_TOPIC, V3_ROUTERS,
                           EXACT_INPUT_TYPES, EXACT_OUTPUT_TYPES, SINGLE_TYPES)

EXACT_IN, EXACT_OUT = "in", "out"


@dataclass
class V3Swap:
    """One executed V3 swap inside a single tick range (one ``Swap`` event).

    ``amount0``/``amount1`` are the *signed* pool perspective amounts (negative in, positive out).
    ``sqrt_price_x96``/``liquidity``/``tick`` are the post-swap state from the event. ``amount_out_min``
    is the victim's slippage guard from the router calldata (None when not a direct router call).
    """
    tx_hash: str
    block_number: int | None
    tx_index: int
    log_index: int
    pool: str
    token0: str
    token1: str
    fee: int
    user: str
    amount0: int          # signed
    amount1: int          # signed
    sqrt_price_x96: int
    liquidity: int
    tick: int
    amount_out_min: int | None = None   # the victim's slippage guard (amountOutMinimum)
    amount_in: int | None = None        # requested amountIn (exact-in), from calldata
    deadline: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def direction(self) -> int:
        """+1 if token0 is sold (amount0<0 → token0 in, token1 out), -1 otherwise."""
        if self.amount0 < 0:
            return 1        # token0 -> token1
        if self.amount0 > 0:
            return -1       # token1 -> token0
        return 1

    @property
    def is_buy(self) -> bool:
        """token1-out (numeraire-side in) — best effort without knowing which token is the numeraire."""
        return self.amount1 > 0


def _selector(data: bytes) -> str:
    return "0x" + data[:4].hex() if len(data) >= 4 else ""


def _decode_path(path_bytes: bytes) -> list[tuple[str, str, int]]:
    """Decode a V3 ``bytes path`` into hop list [(token_in, token_out, fee), ...].

    Each hop is 20-byte tokenIn + 3-byte fee + 20-byte tokenOut. A path has >= 1 hop."""
    hops = []
    i = 0
    while i + 20 + 3 + 20 <= len(path_bytes):
        token_in = "0x" + path_bytes[i:i + 20].hex()
        fee = int.from_bytes(path_bytes[i + 20:i + 23], "big")
        token_out = "0x" + path_bytes[i + 23:i + 43].hex()
        hops.append((token_in, token_out, fee))
        i += 23
    return hops


def _decode_exact_input(data: bytes) -> dict | None:
    """Decode ``exactInput(bytes path, address recipient, uint256 deadline, uint256 amountIn,
    uint256 amountOutMinimum)`` into the victim's guard + first-hop tokens."""
    from eth_abi import decode as abi_decode
    try:
        path, _recipient, deadline, amount_in, amount_out_min = abi_decode(EXACT_INPUT_TYPES, data)
    except Exception:
        return None
    hops = _decode_path(bytes(path))
    if not hops:
        return None
    tok_in, tok_out, fee = hops[0]
    return {"fn": "exactInput", "token_in": tok_in, "token_out": tok_out, "fee": fee,
            "amount_in": int(amount_in), "amount_out_min": int(amount_out_min),
            "deadline": int(deadline), "n_hops": len(hops)}


def _decode_exact_output(data: bytes) -> dict | None:
    from eth_abi import decode as abi_decode
    try:
        path, _recipient, deadline, amount_out, amount_in_max = abi_decode(EXACT_OUTPUT_TYPES, data)
    except Exception:
        return None
    hops = _decode_path(bytes(path))
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
    or None when the tx is not a recognised direct V3 swap. Handles: exactInputSingle,
    exactOutputSingle, exactInput/exactOutput (bytes path), and the ``multicall`` wrapper that
    SwapRouter02 prepends. Defensive: any decode failure returns None (never trust a single field).
    """
    from eth_abi import decode as abi_decode
    to = (tx.get("to") or "").lower()
    if to not in {k.lower() for k in V3_ROUTERS}:
        return None
    inp = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = _selector(data)
    fn = V3_SELECTORS.get(sel)
    if fn is None:
        return None

    # Unwrap a SwapRouter02 multicall: payload = (deadline, bytes[] data); the first non-trivial
    # inner call is the actual swap.
    if fn == "multicall":
        try:
            _deadline, inner_calls = abi_decode(["uint256", "bytes[]"], data[4:])
        except Exception:
            return None
        for inner in inner_calls:
            if not inner or len(inner) < 4:
                continue
            inner_sel = _selector(bytes(inner))
            inner_fn = V3_SELECTORS.get(inner_sel)
            if inner_fn in ("exactInputSingle", "exactOutputSingle", "exactInput"):
                return _decode_inner(inner_fn, bytes(inner)[4:], tx)
        return None

    return _decode_inner(fn, data[4:], tx)


def _decode_inner(fn: str, payload: bytes, tx: dict[str, Any]) -> dict | None:
    from eth_abi import decode as abi_decode
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


def decode_v3_swap_logs(tx_hash: str, block_number: int | None, tx_index: int, logs: list[dict],
                         calldata: dict | None = None,
                         token_by_pool: dict[str, tuple[str, str, int]] | None = None) -> list[V3Swap]:
    """Decode V3 ``Swap`` events in ``logs`` into ``V3Swap`` records, attaching the victim's guard."""
    out: list[V3Swap] = []
    tbp = token_by_pool or {}
    for i, lg in enumerate(logs):
        if not isinstance(lg, dict):
            continue
        topics = lg.get("topics") or []
        if not topics or topics[0].lower() != V3_SWAP_EVENT_TOPIC or lg.get("address") is None:
            continue
        pool = lg["address"]
        t0, t1, fee = tbp.get(pool, ("", "", 0))
        data = bytes.fromhex((lg.get("data") or "0x")[2:].replace("0x", ""))
        try:
            from eth_abi import decode as abi_decode
            amount0, amount1, sqrt_price, liquidity, tick = abi_decode(
                ["int256", "int256", "uint160", "uint128", "int24"], data)
        except Exception:
            continue
        # The Swap event's sender is topics[1]; topics[2] is the recipient.
        sender = topics[1] if len(topics) > 1 else None
        out.append(V3Swap(
            tx_hash=tx_hash, block_number=block_number, tx_index=tx_index, log_index=i,
            pool=pool, token0=t0, token1=t1, fee=int(fee), user="",
            amount0=int(amount0), amount1=int(amount1),
            sqrt_price_x96=int(sqrt_price), liquidity=int(liquidity), tick=int(tick),
            amount_out_min=calldata.get("amount_out_min") if calldata else None,
            amount_in=calldata.get("amount_in") if calldata else None,
            deadline=calldata.get("deadline") if calldata else None,
            raw={"sender": sender},
        ))
    return out
