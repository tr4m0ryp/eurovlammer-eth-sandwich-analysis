"""Which optimal-like buys WE could have front-run: lag, router and tip decide the tier."""
from __future__ import annotations

from collections import Counter, deque

from .tier import interesting, out_row, tier


class LiveVisibility:
    def __init__(self, recent: int = 300):
        self.recent: deque[dict] = deque(maxlen=recent)
        self.counts: Counter[str] = Counter()

    def add(self, slot: int, lag: int, rows: list[dict]) -> list[dict]:
        """Tier each interesting buy; return the visible and marginal rows."""
        out = []
        for r in filter(interesting, rows):
            vis = tier(r, lag)
            self.counts[vis] += 1
            row = out_row(r, lag, vis)
            self.recent.append(row)
            if vis != "invisible":
                out.append(row)
        return out

    def stats(self) -> dict:
        c = self.counts
        return {"n": c.total(), "visible": c["visible"], "marginal": c["marginal"], "invisible": c["invisible"]}
