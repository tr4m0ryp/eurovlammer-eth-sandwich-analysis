"""Victim scoring for the V3 screener: guard recovery, concentrated-liquidity exposure, funnel counts.

Mixed into ``V3Screener``; expects ``self.funnel``, ``self.guard_est`` and ``self.pool_freq``.
"""
from __future__ import annotations

import os
import time

from ....measure.exposure.v3.mesp import V3Pool, exposure

CAPITAL_WEI = int(float(os.environ.get("MEV_FLASH_LOAN_ETH", "10")) * 1e18)  # front cap = flash loan


class ScoringMixin:
    def _actual_amounts(self, s) -> tuple[int, int] | None:
        """The victim's actual (amount_in, amount_out) from the ``Swap`` event's signed amounts."""
        if s.amount0 > 0 and s.amount1 < 0:
            return s.amount0, -s.amount1        # token0 in, token1 out
        if s.amount0 < 0 and s.amount1 > 0:
            return s.amount1, -s.amount0        # token1 in, token0 out
        return None

    def _reconstruct_victim(self, s) -> tuple[int, int] | None:
        """Default approach: (amount_in, amount_out_min) from the event + a *learned* default guard
        slack (data-driven, not a fixed 1%)."""
        amts = self._actual_amounts(s)
        if amts is None:
            return None
        amount_in, amount_out = amts
        if amount_in <= 0 or amount_out <= 0:
            return None
        amount_out_min = self.guard_est.estimate_guard(amount_out)
        return amount_in, amount_out_min

    def _score(self, s, state: dict, origin: str, number: int) -> dict | None:
        # Build the V3Pool from on-chain state; tick bitmap is fetched lazily (empty here = single
        # active range; fine for the current-tick exposure).
        if not state.get("sqrt_price_x96"):
            return None
        self.funnel["decoded"] += 1
        if not state.get("liquidity"):
            return None
        self.funnel["with_pool_state"] += 1
        pool = V3Pool(
            sqrt_price_x96=state["sqrt_price_x96"], liquidity=state["liquidity"],
            tick=state["tick"], tick_spacing=state["tick_spacing"], fee_ppm=state["fee_ppm"],
            ticks={}, token0=state.get("token0", ""), token1=state.get("token1", ""),
            numeraire_is_token0=True,
        )
        # Exact router guard first; when it's absent, fall back to the learned default guard.
        via_default = False
        if s.amount_in is None or s.amount_out_min is None:
            rec = self._reconstruct_victim(s)
            if rec is None:
                return None
            amount_in, amount_out_min = rec
            via_default = True
            self.funnel["default_decoded"] += 1
        else:
            amount_in, amount_out_min = s.amount_in, s.amount_out_min
            # Observe this decodable swap's realized guard slack (single-hop only: for multi-hop the
            # per-hop event output is a different token than the final amountOutMinimum).
            n_hops = (s.raw or {}).get("n_hops")
            if n_hops in (None, 1):
                amts = self._actual_amounts(s)
                if amts is not None:
                    self.guard_est.observe(amts[1], amount_out_min)
            if (s.raw or {}).get("via_aggregator"):
                self.funnel["aggregator_decoded"] += 1
            else:
                self.funnel["router_decoded"] += 1
        if amount_out_min == 0:
            self.funnel["guardless"] += 1
            return None
        victim = {"zero_for_one": s.direction > 0, "amount_in": amount_in, "limit": amount_out_min}
        e = exposure(pool, victim, capital=CAPITAL_WEI)
        if e.mesp <= 0 or e.front <= 0:
            self.funnel["mesp_zero"] += 1
            return None
        # Sanity bounds: reject garbage exposure (Q96/unit-mismatch overflow from the private-router
        # decoders can yield mesp up to 1e58). A sandwich can never extract more than ~10× its front,
        # and a victim's slippage slack is never a multi-thousand-x multiplier.
        if e.mesp > e.front * 100 or not (0.0 < e.slack < 100.0):
            self.funnel["mesp_zero"] += 1
            return None
        self.funnel["mesp_positive"] += 1
        self.pool_freq[s.pool] = self.pool_freq.get(s.pool, 0) + 1
        return {
            "tx_hash": s.tx_hash, "block": number, "pool": s.pool,
            "fee_ppm": state.get("fee_ppm") or s.fee, "price": pool.spot_price, "amount_in": amount_in,
            "amount_out_min": amount_out_min, "mesp": e.mesp, "front": e.front,
            "roi": e.mesp / e.front if e.front else 0.0, "slack": e.slack,
            "pool_freq": self.pool_freq[s.pool], "zero_for_one": s.direction > 0,
            "via_default": via_default,
            "seen_ms": int(time.time() * 1000),
        }
