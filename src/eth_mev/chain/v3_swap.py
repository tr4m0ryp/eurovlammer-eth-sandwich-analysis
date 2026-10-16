"""Uniswap V3 swap decoding (calldata + ``Swap`` event logs) and the concentrated-liquidity record.

A V3 pool has liquidity concentrated in discrete tick ranges; the ``Swap`` event carries the signed
amounts (``amount0``/``amount1``, negative = in, positive = out), the post-swap ``sqrtPriceX96``,
``liquidity`` and ``tick``. The victim's slippage guard is ``amountOutMinimum`` in the router calldata
(SwapRouter02 ``exactInputSingle``), the V3 analogue of V2's ``amountOutMin``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .v3_constants import V3_SELECTORS, V3_SWAP_EVENT_TOPIC, V3_ROUTERS

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


def decode_v3_router(tx: dict[str, Any]) -> dict | None:
    """Decode a single-hop V3 ``exactInputSingle`` from a SwapRouter02 call.

    Returns the victim's ``(amount_in, amount_out_min, token0, token1, fee, deadline)`` or None when
    the tx is not a recognised V3 single-hop swap. Defensive: only the exact-input single-hop shape is
    decoded; multi-hop/aggregator-encoded paths are skipped (never trust a single field).
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
    # exactInputSingle params: (tokenIn, tokenOut, fee, recipient, deadline, amountIn, amountOutMinimum, sqrtPriceLimitX96)
    try:
        (token_in, token_out, fee, _recipient, deadline, amount_in, amount_out_min, _sqrt_limit) = abi_decode(
            ["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
            data[4:],
        )
    except Exception:
        return None
    return {
        "fn": fn,
        "token_in": token_in,
        "token_out": token_out,
        "fee": int(fee),
        "amount_in": int(amount_in),
        "amount_out_min": int(amount_out_min),
        "deadline": int(deadline),
        "user": tx.get("from", ""),
        "tx_hash": tx.get("hash", ""),
    }


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
