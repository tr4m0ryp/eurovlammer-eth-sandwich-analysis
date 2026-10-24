"""Strict screener: the visible swaps our limited capital can actually sandwich (V2).

A swap is strict when it is decoded, gas-feasible, the exposure recomputed with the budget as the
front-leg cap clears ``MIN_PROFIT_WEI``, the front fits the budget, and the return on that front is
at least ``MIN_ROI``. ``pool_freq`` counts strict swaps on the same pool in the last ``FREQ_BLOCKS``.
Adapted from ``tlive/screen/visibility/strict.py`` with the ETH field remap.
"""
from __future__ import annotations

from collections import deque

from ..metrics.v2.exposure import exposure

BUDGET_WEI = 1_000_000_000_000_000_000   # 1 ETH
MIN_PROFIT_WEI = 10_000_000               # 0.01 ETH
MIN_ROI = 0.01
FREQ_BLOCKS = 12                          # ~2.5 minutes on mainnet
MODEL_THRESHOLD = 0.02
MIN_MESP_INTEREST = 10_000_000


def interesting(row: dict) -> bool:
    """A swap worth scoring: has a model score or a meaningful MESP."""
    p = row.get("p") or 0.0
    mesp = row.get("mesp") or 0.0
    return p >= MODEL_THRESHOLD or mesp >= MIN_MESP_INTEREST


class StrictScreener:
    def __init__(self, budget_wei: int = BUDGET_WEI, recent: int = 300):
        self.budget = budget_wei
        self.recent: deque[dict] = deque(maxlen=recent)
        self.pool_blocks: dict[str, deque[int]] = {}
        self.n = self.n_visible = self.n_strict = 0

    def _freq(self, pool: str, block: int) -> int:
        q = self.pool_blocks.setdefault(pool, deque())
        q.append(block)
        while q[0] <= block - FREQ_BLOCKS:
            q.popleft()
        return len(q)

    def _prune(self, block: int) -> None:
        for pool in [p for p, q in self.pool_blocks.items() if q[-1] <= block - FREQ_BLOCKS]:
            del self.pool_blocks[pool]

    def add(self, block: int, rows: list[dict]) -> list[dict]:
        """Return the rows that pass every strict gate."""
        out = []
        for r in filter(interesting, rows):
            self.n += 1
            x = float(r.get("x_pre") or 0)
            y = float(r.get("y_pre") or 0)
            try:
                e = exposure(_swap(r), x=x, y=y, capital=float(self.budget))
            except (ArithmeticError, ValueError):
                continue
            if e.mesp < MIN_PROFIT_WEI or not 0 < e.front <= self.budget or e.mesp / e.front < MIN_ROI:
                continue
            row = dict(r)
            row.update(mesp_budget=int(e.mesp), front_budget=int(e.front),
                       position_wei=int(e.front), expected_profit_wei=int(e.mesp),
                       roi=round(e.mesp / e.front, 4), pool_freq=self._freq(r.get("pool", ""), block),
                       slack=e.slack, mesp=e.mesp)
            self.recent.append(row)
            self.n_strict += 1
            out.append(row)
        if block % 100 == 0:
            self._prune(block)
        return out

    def stats(self) -> dict:
        return {"n": self.n, "visible": self.n_visible, "strict": self.n_strict}


def _swap(d: dict):
    """Minimal Swap the exposure math needs, rebuilt from a row."""
    from ..chain.v2.swaps import Swap
    opt = lambda k: int(d[k]) if d.get(k) is not None else None  # noqa: E731
    return Swap(
        tx_hash=d.get("tx_hash", ""), block_number=d.get("block"), tx_index=d.get("tx_index", 0),
        log_index=d.get("log_index", 0), venue=d.get("venue", ""), pool=d.get("pool", ""),
        token0=d.get("token0", ""), token1=d.get("token1", ""), user=d.get("user", ""),
        is_buy=bool(d.get("is_buy")), amount_in=int(d.get("amount_in") or 0),
        amount_out=int(d.get("amount_out") or 0), x_pre=float(d.get("x_pre") or 0),
        y_pre=float(d.get("y_pre") or 0), fee_bps=float(d.get("fee_bps") or 30),
        kind=d.get("kind", "in"), amount=opt("amount"), limit=opt("limit"),
        deadline=d.get("deadline"))
