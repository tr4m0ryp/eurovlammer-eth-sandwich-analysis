"""Budget-constrained screener: the visible buys our limited capital can actually sandwich.

A buy is strict when we see it in time (``tier`` is not invisible), the sandwich recomputed with the
budget as the front-leg cap clears ``MIN_PROFIT_LAMPORTS``, the front leg fits the budget, and the
return on that front leg is at least ``MIN_ROI``. ``pool_freq`` counts strict buys on the same pool
in the last ``FREQ_WINDOW`` slots: a repeatable pool is worth more than a one-off.
"""
from __future__ import annotations

from collections import deque

from ...chain.swaps.model import Swap
from ...metrics.exposure import exposure
from .tier import interesting, out_row, tier

BUDGET_LAMPORTS = 500_000_000   # 0.5 SOL, about 100 USDT
MIN_PROFIT_LAMPORTS = 50_000    # two legs of gas plus a tip
MIN_ROI = 0.01                  # profit / front leg
FREQ_WINDOW = 300               # slots, about two minutes


def _swap(d: dict) -> Swap:
    """The minimal Swap the exposure math needs, rebuilt from a scored row."""
    opt = lambda k: int(d[k]) if d.get(k) is not None else None
    return Swap(signature=d.get("signature", ""), slot=int(d.get("slot", 0)), tx_index=int(d.get("tx_index", 0)),
                ev_index=int(d.get("ev_index", 0)), venue=d.get("venue", ""), pool=d.get("pool", ""),
                mint=d.get("mint", ""), user=d.get("signer", ""), is_buy=bool(d.get("is_buy")), sol=int(d.get("sol", 0)),
                tok=0, x_pre=float(d.get("x_ref", 0)), y_pre=float(d.get("y_pre", 0)), fee_bps=float(d.get("fee_bps", 0)),
                kind=d.get("kind", "in"), amount=opt("amount"), limit=opt("limit"), ix_name=d.get("ix_name", ""),
                block_time=opt("block_time") or None)


class StrictVisibility:
    def __init__(self, budget_lamports: int = BUDGET_LAMPORTS, recent: int = 300):
        self.budget = budget_lamports
        self.recent: deque[dict] = deque(maxlen=recent)
        self.pool_slots: dict[str, deque[int]] = {}
        self.n = self.n_visible = self.n_strict = 0

    def _freq(self, pool: str, slot: int) -> int:
        q = self.pool_slots.setdefault(pool, deque())
        q.append(slot)
        while q[0] <= slot - FREQ_WINDOW:
            q.popleft()
        return len(q)

    def _prune(self, slot: int) -> None:
        """Drop pools with no strict buy inside the window, so the map does not grow with every pool seen."""
        for pool in [p for p, q in self.pool_slots.items() if q[-1] <= slot - FREQ_WINDOW]:
            del self.pool_slots[pool]

    def add(self, slot: int, lag: int, rows: list[dict]) -> list[dict]:
        """Return the rows that pass every strict gate."""
        out = []
        for r in filter(interesting, rows):
            self.n += 1
            vis = tier(r, lag)
            if vis != "visible":
                continue
            self.n_visible += vis == "visible"
            try:
                e = exposure(_swap(r), capital=float(self.budget))
            except (ArithmeticError, ValueError):
                continue
            if e.mesp < MIN_PROFIT_LAMPORTS or not 0 < e.front <= self.budget or e.mesp / e.front < MIN_ROI:
                continue
            row = out_row(r, lag, vis, vis_strict=True, mesp_budget=int(e.mesp), front_budget=int(e.front),
                          roi=round(e.mesp / e.front, 4), pool_freq=self._freq(r.get("pool", ""), slot))
            self.recent.append(row)
            self.n_strict += 1
            out.append(row)
        if slot % 100 == 0:
            self._prune(slot)
        return out

    def stats(self) -> dict:
        return {"n": self.n, "visible": self.n_visible, "strict": self.n_strict}
