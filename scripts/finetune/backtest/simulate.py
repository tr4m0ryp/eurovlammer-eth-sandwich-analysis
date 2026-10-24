"""Replay simulation: feed the strict event stream through the V2 allocator (sampled or expected P&L)."""
from __future__ import annotations

import statistics

from eth_mev.engine.allocation.v2 import WEI, Allocator


RISK_DEFAULTS = {"MAX_SLIPPAGE_FRAC": 0.05, "MIN_COUNTERPARTY_P": 0.02, "MAX_PRIORITY_FEE_WEI": 50_000_000_000}


def _risk_ok(r: dict, p: dict) -> bool:
    kappa = float(r.get("front_budget") or 0)
    depth = float(r.get("x_ref") or 0)
    slip = kappa / depth if depth > 0 else float("inf")
    if slip > p.get("MAX_SLIPPAGE_FRAC", RISK_DEFAULTS["MAX_SLIPPAGE_FRAC"]):
        return False
    if (r.get("p") or 0) < p.get("MIN_COUNTERPARTY_P", RISK_DEFAULTS["MIN_COUNTERPARTY_P"]):
        return False
    prio = r.get("priority_fee")
    prio = int(prio) if prio is not None and not (isinstance(prio, float) and prio != prio) else 0
    if prio > p.get("MAX_PRIORITY_FEE_WEI", RISK_DEFAULTS["MAX_PRIORITY_FEE_WEI"]):
        return False
    return True


def run(replay: dict, params: dict, seed: int = 0) -> dict:
    alloc = Allocator(initial_eth=1.0, budget_eth=1.0, seed=seed)
    alloc.HISTORY_MAX = 10 ** 9
    for k, v in params.items():
        if k in ("MAX_SLIPPAGE_FRAC", "MIN_COUNTERPARTY_P", "MAX_PRIORITY_FEE_WEI"):
            continue
        if k == "CAPTURE_FRACTION" and v is not None:
            cf = float(v)
            alloc._capture_fraction = lambda pi: cf
        else:
            setattr(alloc, k, v)
    alloc.set_settlement(replay["settlement"])
    peak = 1.0
    max_dd = 0.0
    for block, rows in replay["blocks"]:
        kept = [r for r in rows if _risk_ok(r, params)]
        if kept:
            alloc.step(int(block), kept)
        equity = alloc.wallet_eth() + alloc._eth(alloc.total_locked)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / 1.0)
    gains = [h["gain"] / WEI for h in alloc.history]
    n = len(gains)
    mean = sum(gains) / n if n else 0.0
    var = sum((g - mean) ** 2 for g in gains) / n if n else 0.0
    sharpe = mean / (var ** 0.5) if var > 0 else 0.0
    s = alloc.state()
    raced = alloc.n_wins + alloc.n_outbid
    return {
        "commits": alloc.n_committed, "wins": alloc.n_wins, "outbid": alloc.n_outbid,
        "no_sandwich": alloc.n_no_sandwich, "sandwiched": alloc.n_sandwiched,
        "completed": alloc.n_completed, "total_profit_eth": s["total_profit"],
        "max_drawdown": max_dd, "sharpe": sharpe,
        "win_rate": alloc.n_wins / raced if raced else 0.0,
    }


def run_mc(replay: dict, params: dict, seeds: int = 5) -> dict:
    rows = [run(replay, params, seed=s) for s in range(seeds)]
    out = {k: statistics.mean(r[k] for r in rows)
           for k in ("commits", "wins", "outbid", "no_sandwich", "sandwiched", "completed",
                     "total_profit_eth", "max_drawdown", "sharpe")}
    return out


class ExpectedAllocator(Allocator):
    """Deterministic expected-P&L resolution (no RNG), so the sweep is noise-free."""

    def _resolve(self, block: int, events: list[dict]) -> None:
        due = [k for k, p in self.active.items() if p["unlock_block"] <= block]
        for key in due:
            pos = self.active.pop(key)
            pool = pos["pool"]
            self.active_pools[pool] -= 1
            if self.active_pools[pool] <= 0:
                del self.active_pools[pool]
            hit = self.settlement.get(pos["key"])
            if hit is not None:
                self.n_sandwiched += 1
                self.sum_realized += hit[2]
                realized = int(pos["pi"] * self._capture_fraction(pos["pi"]))
                cost = self._cost_of_landing(pos)
                win_gain = max(0, realized - cost)
                loss_gain = -cost
                self.n_wins += self.WIN_RATE
                self.n_outbid += (1.0 - self.WIN_RATE)
                gain = int(round(self.WIN_RATE * win_gain + (1.0 - self.WIN_RATE) * loss_gain))
            else:
                gain = -int(pos["kappa"] * self.ADVERSE) - self.GAS_WEI
            self.wallet += pos["kappa"] + gain
            self.total_locked -= pos["kappa"]
            self.total_profit += gain
            self.n_completed += 1
            self.n_no_sandwich += not hit


def run_expected(replay: dict, params: dict, seed: int = 0) -> dict:
    alloc = ExpectedAllocator(initial_eth=1.0, budget_eth=1.0, seed=seed)
    alloc.HISTORY_MAX = 10 ** 9
    for k, v in params.items():
        if k in ("MAX_SLIPPAGE_FRAC", "MIN_COUNTERPARTY_P", "MAX_PRIORITY_FEE_WEI"):
            continue
        if k == "CAPTURE_FRACTION" and v is not None:
            cf = float(v)
            alloc._capture_fraction = lambda pi: cf
        else:
            setattr(alloc, k, v)
    alloc.set_settlement(replay["settlement"])
    peak = 1.0
    max_dd = 0.0
    for block, rows in replay["blocks"]:
        kept = [r for r in rows if _risk_ok(r, params)]
        if kept:
            alloc.step(int(block), kept)
        equity = alloc.wallet_eth() + alloc._eth(alloc.total_locked)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / 1.0)
    s = alloc.state()
    raced = alloc.n_wins + alloc.n_outbid
    return {"commits": alloc.n_committed, "wins": alloc.n_wins, "outbid": alloc.n_outbid,
            "no_sandwich": alloc.n_no_sandwich, "sandwiched": alloc.n_sandwiched,
            "completed": alloc.n_completed, "total_profit_eth": s["total_profit"],
            "max_drawdown": max_dd, "wallet": s["wallet"],
            "win_rate": alloc.n_wins / raced if raced else 0.0}
