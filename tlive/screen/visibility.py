"""Live visibility tracker and strict sandwich screener for budget-constrained trading.

Two tiers:
  LiveVisibility     which optimal buys WE could have seen in time (lag, router, tip).
  StrictVisibility   among those, which are actually feasible with a limited capital budget
                     (default 0.5 SOL ≈ 100 USDT) — recomputes MESP with the real budget cap.

A buy is "strict" when:
  1. It is "visible" or "marginal" from LiveVisibility (we see it in time)
  2. Its budget-constrained front-run size fits within our capital
  3. The profit after fees is worth the capital lock-up (ROI > threshold)
  4. Similar opportunities appear frequently on the same pool (repeatable)
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from ..chain.swaps.model import Swap
from ..metrics.exposure import exposure_budget

# Aggregator routers that hide the underlying tx
AGGREGATORS = ("JUP6LkbZ", "6Vo3245e", "term9YPb", "L2TExMFKdjpN")
# Our max lag to still be able to front-run
MAX_LAG_VISIBLE = 3
MAX_LAG_MARGINAL = 8
# Capital budget: 0.5 SOL ≈ 100 USDT at ~$170/SOL
BUDGET_LAMPORTS = 500_000_000
# Minimum profit to bother (gas cost of two legs + tip)
MIN_PROFIT_LAMPORTS = 50_000
# Model score threshold — approximate top 10% of risk buys
MODEL_THRESHOLD = 0.02
# Minimum ROI to be worth the capital lock-up
MIN_ROI = 0.01              # 1% return per sandwich
# Fields needed from the swap dict for exposure computation
KEEP = ("signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
        "is_buy", "sol", "x_ref", "y_pre", "fee_bps", "slack", "mesp", "front_opt", "phi", "p", "flag",
        "cu_price", "jito_tip", "kind", "amount", "limit", "tx_index")


def _swap_from_dict(d: dict) -> Swap:
    """Build a minimal Swap from a row dict for exposure computation."""
    return Swap(
        signature=d.get("signature", ""), slot=int(d.get("slot", 0)),
        tx_index=int(d.get("tx_index", 0)), ev_index=int(d.get("ev_index", 0)),
        venue=d.get("venue", ""), pool=d.get("pool", ""), mint=d.get("mint", ""),
        user=d.get("signer", ""), is_buy=bool(d.get("is_buy", False)),
        sol=int(d.get("sol", 0)), tok=0,
        x_pre=float(d.get("x_ref", 0)), y_pre=float(d.get("y_pre", 0)),
        fee_bps=float(d.get("fee_bps", 0)), kind=d.get("kind", "in"),
        amount=int(d["amount"]) if d.get("amount") is not None else None,
        limit=int(d["limit"]) if d.get("limit") is not None else None,
        ix_name=d.get("ix_name", ""), block_time=int(d["block_time"]) if d.get("block_time") else None,
    )


class LiveVisibility:
    """Tracks optimal-like buys and marks which ones WE could have front-run."""

    def __init__(self, recent: int = 300):
        self.recent: deque[dict] = deque(maxlen=recent)
        self.n = self.n_visible = self.n_marginal = self.n_invisible = 0

    def add(self, slot: int, lag: int, rows: list[dict]) -> list[dict]:
        """Score each buy row for OUR visibility. Returns visible + marginal rows."""
        out = []
        for r in rows:
            if not r.get("is_buy"):
                continue
            # Only consider buys that are interesting (high model score + sandwichable)
            p = r.get("p", 0) or 0
            mesp = r.get("mesp", 0) or 0
            if p < MODEL_THRESHOLD and mesp < 10_000:
                continue

            # Compute visibility
            tipped = (r.get("jito_tip") or 0) > 0
            router = r.get("router") or ""
            routed = router != "" and router[:8] in AGGREGATORS

            if lag <= MAX_LAG_VISIBLE and not tipped and not routed:
                vis = "visible"
            elif lag <= MAX_LAG_MARGINAL and (tipped or routed):
                vis = "marginal"
            elif lag <= MAX_LAG_VISIBLE and (tipped or routed):
                vis = "marginal"
            elif lag <= MAX_LAG_MARGINAL and not tipped and not routed:
                vis = "marginal"
            else:
                vis = "invisible"

            self.n += 1
            if vis == "visible":
                self.n_visible += 1
            elif vis == "marginal":
                self.n_marginal += 1
            else:
                self.n_invisible += 1

            row = {k: r.get(k) for k in KEEP}
            row.update(vis_lag=lag, vis=vis, seen_ms=int(time.time() * 1000))
            self.recent.append(row)
            if vis in ("visible", "marginal"):
                out.append(row)
        return out

    def stats(self) -> dict:
        return {"n": self.n, "visible": self.n_visible, "marginal": self.n_marginal,
                "invisible": self.n_invisible}


class StrictVisibility:
    """Budget-constrained sandwich screener for a real proof-of-concept bot.

    From the pool of visible buys, only keeps those that are actually feasible
    with a limited capital budget (default 0.5 SOL). Recomputes MESP with the
    real budget cap and filters by ROI and repeatability.

    The goal: find transactions that appear frequently but are not frequently
    accessible — high ROI opportunities that our limited capital can actually capture.
    """

    def __init__(self, budget_lamports: int = BUDGET_LAMPORTS, recent: int = 300):
        self.budget = budget_lamports
        self.recent: deque[dict] = deque(maxlen=recent)
        # Track pool frequency: pool -> deque of (slot, budget_mesp, budget_front)
        self.pool_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.n = self.n_visible = self.n_strict = 0

    def add(self, slot: int, lag: int, rows: list[dict]) -> list[dict]:
        """Score each buy row for budget-constrained sandwichability.

        Returns only the rows that pass ALL strict filters.
        """
        out = []
        for r in rows:
            if not r.get("is_buy"):
                continue
            p = r.get("p", 0) or 0
            mesp_full = r.get("mesp", 0) or 0
            if p < MODEL_THRESHOLD and mesp_full < 10_000:
                continue

            # Visibility check (same logic as LiveVisibility)
            tipped = (r.get("jito_tip") or 0) > 0
            router = r.get("router") or ""
            routed = router != "" and router[:8] in AGGREGATORS

            if lag <= MAX_LAG_VISIBLE and not tipped and not routed:
                vis = "visible"
            elif lag <= MAX_LAG_MARGINAL and (tipped or routed):
                vis = "marginal"
            elif lag <= MAX_LAG_VISIBLE and (tipped or routed):
                vis = "marginal"
            elif lag <= MAX_LAG_MARGINAL and not tipped and not routed:
                vis = "marginal"
            else:
                vis = "invisible"

            if vis == "invisible":
                self.n += 1
                continue

            self.n += 1
            if vis == "visible":
                self.n_visible += 1

            # Compute budget-constrained MESP
            try:
                swap = _swap_from_dict(r)
                e = exposure_budget(swap, float(self.budget))
                mesp_budget = e.mesp
                front_budget = e.front
            except Exception:
                mesp_budget = 0.0
                front_budget = 0.0

            # Filter 1: minimum profit
            if mesp_budget < MIN_PROFIT_LAMPORTS:
                continue

            # Filter 2: front-run must fit within budget
            if front_budget > self.budget or front_budget <= 0:
                continue

            # Compute ROI
            roi = mesp_budget / front_budget if front_budget > 0 else 0.0

            # Filter 3: minimum ROI
            if roi < MIN_ROI:
                continue

            # Track pool frequency (how often similar opportunities arise)
            pool = r.get("pool", "")
            ph = self.pool_history[pool]
            ph.append((slot, mesp_budget, front_budget))

            # Opportunity frequency: how many budget-sandwichable buys on this pool
            # in the last 300 slots (~2 minutes)
            freq = sum(1 for s, _, _ in ph if s > slot - 300)

            # Build output row
            row = {k: r.get(k) for k in KEEP}
            row.update(vis_lag=lag, vis=vis, vis_strict=True,
                       mesp_budget=int(mesp_budget), front_budget=int(front_budget),
                       roi=round(roi, 4), pool_freq=freq,
                       seen_ms=int(time.time() * 1000))
            self.recent.append(row)
            self.n_strict += 1
            out.append(row)
        return out

    def stats(self) -> dict:
        return {"n": self.n, "visible": self.n_visible, "strict": self.n_strict}