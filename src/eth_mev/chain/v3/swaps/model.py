"""The V3 swap record: one executed swap inside a single tick range (one ``Swap`` event).

A V3 pool has liquidity concentrated in discrete tick ranges; the ``Swap`` event carries the signed
amounts (``amount0``/``amount1``, negative = in, positive = out), the post-swap ``sqrtPriceX96``,
``liquidity`` and ``tick``. The victim's slippage guard is ``amountOutMinimum`` in the router calldata
(SwapRouter02 ``exactInputSingle``), the V3 analogue of V2's ``amountOutMin``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
        """+1 if the swap is token0→token1 (zeroForOne), -1 if token1→token0.

        V3 ``Swap`` event sign convention (verified against a traced pool ``swap`` call): a POSITIVE
        ``amount0`` means token0 flowed INTO the pool (the trader paid token0) → token0→token1 →
        ``zeroForOne=True``. A NEGATIVE ``amount0`` means token0 flowed OUT (trader received token0).
        """
        if self.amount0 > 0:
            return 1        # token0 into pool → token0→token1
        if self.amount0 < 0:
            return -1       # token0 out of pool → token1→token0
        return 1

    @property
    def is_buy(self) -> bool:
        """token1-out (numeraire token0 in) — True when the trader pays token0 and receives token1."""
        return self.amount1 < 0
