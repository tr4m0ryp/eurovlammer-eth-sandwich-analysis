"""The shared visibility rule and the row shape both books publish."""
from __future__ import annotations

import time

AGGREGATORS = ("JUP6LkbZ", "6Vo3245e", "term9YPb", "L2TExMFKdjpN")   # routers that hide the inner swap
MAX_LAG_VISIBLE = 3         # slots behind the tip at which we can still land a front leg
MAX_LAG_MARGINAL = 8
MODEL_THRESHOLD = 0.02      # roughly the top 10% of risk buys
MIN_MESP_INTEREST = 10_000  # lamports: below this and under the threshold, a buy is not worth tiering
KEEP = ("signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
        "is_buy", "sol", "x_ref", "y_pre", "fee_bps", "slack", "mesp", "front_opt", "phi", "p", "flag",
        "cu_price", "jito_tip", "kind", "amount", "limit", "tx_index")


def interesting(r: dict) -> bool:
    """A buy worth tiering: a high model score or a sandwich gain above dust."""
    return bool(r.get("is_buy")) and ((r.get("p") or 0) >= MODEL_THRESHOLD or (r.get("mesp") or 0) >= MIN_MESP_INTEREST)


def tier(r: dict, lag: int) -> str:
    """``visible``: we are close to the tip and nothing shields the buy. ``marginal``: one of the two
    fails but we are within reach. ``invisible``: too far behind the tip."""
    router = r.get("router") or ""
    shielded = (r.get("jito_tip") or 0) > 0 or router[:8] in AGGREGATORS
    if lag <= MAX_LAG_VISIBLE and not shielded:
        return "visible"
    return "marginal" if lag <= MAX_LAG_MARGINAL else "invisible"


def out_row(r: dict, lag: int, vis: str, **extra) -> dict:
    row = {k: r.get(k) for k in KEEP}
    row.update(vis_lag=lag, vis=vis, seen_ms=int(time.time() * 1000), **extra)
    return row
