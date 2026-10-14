"""Live visibility tracker: which optimal buys could WE have seen before they landed.

A buy is "visible to us" when the screener's own latency would have let us
submit a front-run before the buy landed. This is a function of:

  1. Our lag (how many slots behind the tip we are when we see the block)
  2. The submission path: Jito-tipped or routed-through-aggregator buys are
     invisible to us (they land via private mempool or are hidden inside a bundle)
  3. Pool momentum: we only see what the RPC gives us, and we can only front-run
     buys on pools we're actively tracking

The visibility book receives every buy that passes the model's optimal-like
filter (high score + realistic sandwich profit) and immediately marks its
visibility. Visible rows are published as a new event type so the live page
shows only the transactions WE could actually have sandwiched.

Visibility tiers for OUR bot:
  visible   - lag < 4 slots, NOT Jito-tipped, NOT routed through an aggregator
  marginal  - one of: lag 4-8 slots, OR tipped, OR routed
  invisible - two or more of the above, or lag >= 8 slots
"""
from __future__ import annotations

import time
from collections import deque

# Aggregator routers that hide the underlying tx (Jupiter, etc.)
AGGREGATORS = ("JUP6LkbZ", "6Vo3245e", "term9YPb", "L2TExMFKdjpN")
# Our max lag to still be able to front-run (in slots, ~400ms each)
MAX_LAG_VISIBLE = 3      # visible: we're basically at the tip
MAX_LAG_MARGINAL = 8     # marginal: we might have seen it but barely
# Model score threshold — same as the optimal cutoff from research
MODEL_THRESHOLD = 0.02   # approximate: top 10% of risk buys
KEEP = ("signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
        "is_buy", "sol", "x_ref", "y_pre", "fee_bps", "slack", "mesp", "front_opt", "phi", "p", "flag",
        "cu_price", "jito_tip")


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