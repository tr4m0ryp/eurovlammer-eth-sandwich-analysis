"""Atomic cross-pool arbitrage math: the two-leg buy/sell profit and the optimal borrow size.

An arb borrows the numeraire (WETH) via a flash loan, buys the token on the cheap market, sells it on
the expensive market, and repays — all in one tx. This module computes the profit of a candidate
two-leg arb and the borrow amount that maximizes it, for a **unified** ``Market`` that can be a
Uniswap V3 pool (``v3_math.exact_input``) or a V2/Sushi pair (``metrics.curve`` constant-product).

    profit(amount) = sell_out(amount) − amount − flash_fee(amount)

The optimal ``amount`` is found by golden-section over ``[0, loan_cap]``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .exposure.v3 import tick_math as v3
from .exposure.v2 import curve

GOLD = (math.sqrt(5) - 1) / 2
Q96 = 1 << 96


@dataclass
class Market:
    """One tradeable market for a token pair — V3 pool or V2/Sushi pair, in a common shape."""
    token0: str
    token1: str
    kind: str                       # "v3" | "v2"
    fee_bps: int                    # combined fee in basis points (30 = 0.3%)
    address: str = ""
    # V3 state
    sqrt_price_x96: int = 0
    liquidity: int = 0
    tick: int = 0
    tick_spacing: int = 60
    ticks: dict = field(default_factory=dict)
    # V2 state
    reserve0: int = 0
    reserve1: int = 0
    last_updated: int | None = None     # V2 getReserves blockTimestampLast (None for V3 = always fresh)

    @property
    def price(self) -> float:
        """token1 per token0."""
        if self.kind == "v3" and self.sqrt_price_x96:
            return (self.sqrt_price_x96 / Q96) ** 2
        if self.reserve0:
            return self.reserve1 / self.reserve0
        return 0.0

    @property
    def depth_weth(self) -> int:
        """The market's WETH (token0) depth in wei, after orienting WETH-as-token0.

        V2: the WETH reserve. V3: the token0 balance ≈ liquidity × 2^96 / sqrt_price_x96."""
        if self.kind == "v2":
            return self.reserve0
        if self.sqrt_price_x96:
            return self.liquidity * Q96 // self.sqrt_price_x96
        return 0

    def swap_out(self, amount_in: int, zero_for_one: bool) -> int:
        """Swap ``amount_in`` of token0 (zero_for_one=True) or token1 (False) → the other token out."""
        if self.kind == "v3":
            fee_ppm = self.fee_bps * 100
            return v3.exact_input(self.sqrt_price_x96, self.liquidity, self.ticks,
                                  self.tick_spacing, amount_in, zero_for_one, fee_ppm)[0]
        # V2 constant-product
        r0, r1 = self.reserve0, self.reserve1
        fee = self.fee_bps / 10_000
        if zero_for_one:                       # token0 → token1
            net = amount_in * (1 - fee)
            return int(curve.tokens_for_net(r0, r1, net))
        # token1 → token0
        return int(curve.sol_for_tokens(r0, r1, amount_in) * (1 - fee))

    def orient(self, numeraire: str) -> "Market":
        """Return a copy with ``numeraire`` as token0 (invert the price if it is currently token1).

        The arb borrows token0, so every market must be oriented WETH-as-token0 before comparing."""
        if self.token0.lower() == numeraire.lower():
            return self
        # token1 is the numeraire: swap token0<->token1 and invert the price
        if self.kind == "v3":
            return Market(token0=self.token1, token1=self.token0, kind="v3", fee_bps=self.fee_bps,
                          address=self.address,
                          sqrt_price_x96=(Q96 * Q96) // self.sqrt_price_x96 if self.sqrt_price_x96 else 0,
                          liquidity=self.liquidity, tick=-self.tick, tick_spacing=self.tick_spacing,
                          ticks={t: -l for t, l in self.ticks.items()},
                          last_updated=self.last_updated)
        return Market(token0=self.token1, token1=self.token0, kind="v2", fee_bps=self.fee_bps,
                      address=self.address, reserve0=self.reserve1, reserve1=self.reserve0,
                      last_updated=self.last_updated)


@dataclass
class ArbResult:
    borrow_wei: int
    buy_out: int
    sell_out: int
    profit_wei: int
    flash_fee_wei: int
    buy: Market | None = None
    sell: Market | None = None


def two_leg_profit(buy: Market, sell: Market, borrow_wei: int, flash_fee_bps: int = 5) -> tuple[int, int, int]:
    """Borrow token0 → buy token1 on ``buy`` → sell token1 on ``sell`` → token0 back.

    ``buy`` must be the market where token1 is *cheap* (higher token1/token0). Returns
    ``(token1_received, token0_returned, flash_fee)``."""
    token_received = buy.swap_out(borrow_wei, True)
    numeraire_returned = sell.swap_out(token_received, False)
    flash_fee = int(borrow_wei * flash_fee_bps / 10_000)
    return token_received, numeraire_returned, flash_fee


def optimal_arb(buy: Market, sell: Market, loan_cap_wei: int, flash_fee_bps: int = 5,
                min_profit_wei: int = 0) -> ArbResult | None:
    """The borrow amount that maximizes arb profit, capped at ``loan_cap_wei`` (golden-section)."""
    if loan_cap_wei <= 0:
        return None

    def profit(b):
        tok, out, fee = two_leg_profit(buy, sell, b, flash_fee_bps)
        return out - b - fee, tok, out, fee

    lo, hi = 0, loan_cap_wei
    for _ in range(80):
        c = hi - int(GOLD * (hi - lo))
        d = lo + int(GOLD * (hi - lo))
        if profit(c)[0] < profit(d)[0]:
            lo = c
        else:
            hi = d
        if hi - lo <= 2:
            break
    best = (lo + hi) // 2
    p, tok, out, fee = profit(best)
    if p <= min_profit_wei:
        return None
    return ArbResult(best, tok, out, p, fee)


def find_best_arb(markets: list[Market], loan_cap_wei: int, flash_fee_bps: int = 5,
                  min_profit_wei: int = 0) -> ArbResult | None:
    """Compare every same-pair market pair and return the single most profitable arb, or None."""
    best: ArbResult | None = None
    for i in range(len(markets)):
        for j in range(len(markets)):
            if i == j:
                continue
            a, b = markets[i], markets[j]
            if a.token0.lower() != b.token0.lower() or a.token1.lower() != b.token1.lower():
                continue
            buy, sell = (a, b) if a.price > b.price else (b, a)
            r = optimal_arb(buy, sell, loan_cap_wei, flash_fee_bps, min_profit_wei)
            if r is not None:
                r.buy, r.sell = buy, sell
                if best is None or r.profit_wei > best.profit_wei:
                    best = r
    return best
