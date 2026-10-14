"""The venue-neutral swap record every decoder emits and every metric consumes."""
from __future__ import annotations

from dataclasses import dataclass

EXACT_IN, EXACT_OUT = "in", "out"


@dataclass
class Swap:
    """One executed swap on a constant-product curve, in the program's own integers.

    ``x_pre``/``y_pre`` are the EFFECTIVE reserves the program priced against just before this
    swap (pump.fun: virtual reserves; PumpSwap: balance plus any virtual quote, recovered from
    the swap itself). ``sol`` is the curve-side notional, fees excluded; ``fee_bps`` is the sum
    of every fee leg, levied on top of a buy and out of a sell's proceeds.

    The victim's protection is ``(kind, amount, limit)``: for an exact-out buy, ``amount``
    tokens at most ``limit`` lamports; for an exact-in buy, ``amount`` lamports for at least
    ``limit`` tokens; for a sell, ``amount`` tokens for at least ``limit`` lamports.
    ``limit`` is None when the instruction could not be paired with its event.
    """
    signature: str
    slot: int
    tx_index: int
    ev_index: int
    venue: str
    pool: str
    mint: str
    user: str
    is_buy: bool
    sol: int
    tok: int
    x_pre: float
    y_pre: float
    fee_bps: float
    kind: str
    amount: int | None
    limit: int | None
    ix_name: str
    block_time: int | None = None

    @property
    def gross(self) -> float:
        """Lamports the trader actually paid (buy) or received (sell), fees included."""
        r = self.fee_bps / 1e4
        return self.sol * (1 + r) if self.is_buy else self.sol * (1 - r)
