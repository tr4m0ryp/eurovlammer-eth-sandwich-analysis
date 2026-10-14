"""The risk book: every swap that could theoretically be sandwiched, settled against real attacks.

A swap is **risk** when its own limit admits a profitable sandwich: the slack-capped maximum
extractable sandwich profit (MESP, exact constant-product math, the pool's fee paid on both legs)
exceeds ``RISK_MIN_LAMPORTS``, the base fee of the attacker's two transactions. MESP is measured at
the swap's own pre-state and, for a real victim, again at the pool state just before the front leg
(``mesp_ref``: what the attacker actually priced against); either qualifies.

Gain is reported three ways: ``mesp_best`` in lamports, ``gain_pct`` as a share of the victim's
own trade, and ``gain_roi`` as a return on the attacker's optimal front-run size. A sandwiched row
also carries what the attacker actually took (``sw_profit``, the whole sandwich, all victims).

Rows are held per slot and written once, ``SETTLE`` slots later, with their final status
(``sandwiched`` / ``clean``), so the table is insert-only. **Top 10%** marks risk rows whose best
MESP is at or above the running 90th percentile of the session's risk rows; the morning export
recomputes it over the whole night.
"""
from __future__ import annotations

from collections import deque

import numpy as np

RISK_MIN_LAMPORTS = 10_000
KEEP = ("signature", "ev_index", "slot", "block_time", "venue", "pool", "mint", "signer", "router",
        "is_buy", "sol", "x_ref", "fee_bps", "slack", "mesp", "front_opt", "phi", "p", "flag", "cu_price", "jito_tip")


class RiskBook:
    def __init__(self, recent: int = 300):
        self.pending: dict[int, list[dict]] = {}
        self.mesp_all: list[float] = []
        self.q90 = float("inf")
        self.recent: deque[dict] = deque(maxlen=recent)
        self.n = self.n_hit = self.n_top = self.n_top_hit = 0

    def add(self, slot: int, rows: list[dict]) -> None:
        self.pending[slot] = [{k: r.get(k) for k in KEEP} for r in rows]

    def settle(self, settled: int, victims: dict) -> list[dict]:
        """Rows of every slot <= ``settled`` that qualify as risk, with status and top-10% mark."""
        out = []
        for s in sorted(k for k in self.pending if k <= settled):
            for r in self.pending.pop(s):
                hit = victims.get((r["signature"], r["ev_index"]))
                ref = hit[4] if hit else None
                best = max(r["mesp"] or 0.0, ref or 0.0)
                if best <= RISK_MIN_LAMPORTS:
                    continue
                front = hit[5] if hit and (ref or 0) >= (r["mesp"] or 0) else (r["front_opt"] or 0.0)
                sol = r["sol"] or 0
                r.update(mesp_ref=ref, mesp_best=best, front_best=front,
                         gain_pct=best / sol if sol else None,              # of the victim's trade
                         gain_roi=best / front if front else None,          # on the front-run capital
                         taken_pct=(hit[2] / sol) if hit and sol else None, status="sandwiched" if hit else "clean",
                         attacker=hit[1] if hit else None, sw_profit=hit[2] if hit else None)
                out.append(r)
        if out:
            self.mesp_all += [r["mesp_best"] for r in out]
            if len(self.mesp_all) >= 50:
                self.q90 = float(np.quantile(self.mesp_all, 0.9))
            for r in out:
                r["top10"] = r["mesp_best"] >= self.q90
                hit = r["status"] == "sandwiched"
                self.n += 1
                self.n_hit += hit
                self.n_top += r["top10"]
                self.n_top_hit += hit and r["top10"]
                self.recent.append(r)
        return out

    def stats(self) -> dict:
        g = np.array(self.mesp_all) if self.mesp_all else np.zeros(1)
        return {"gain_total_sol": float(g.sum()) / 1e9, "gain_median_sol": float(np.median(g)) / 1e9,
                "n": self.n, "sandwiched": self.n_hit, "top10": self.n_top, "top10_sandwiched": self.n_top_hit,
                "q90_sol": self.q90 / 1e9 if np.isfinite(self.q90) else None,
                "min_sol": RISK_MIN_LAMPORTS / 1e9}
