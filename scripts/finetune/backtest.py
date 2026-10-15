"""Deterministic replay backtest: strict events from the persisted DuckDB fed through ``Allocator``.

Ground truth is the night's own settlement — the ``victims`` table (which buys a real sandwich
actually landed on) — so a run is reproducible and does not touch the live stream. The strict
universe is rebuilt from the ``swaps`` table (which already carries the live scorer's ``p``, ``mesp``,
``phi``, ``slack`` plus every field ``StrictVisibility.add`` re-reads) by replaying only that filter's
math: ``interesting`` → unshielded (visible) → budget-capped ``exposure(swap, capital=0.5 SOL)`` →
``mesp >= MIN_PROFIT`` and ``roi >= MIN_ROI``. The per-pool ``pool_freq`` is recomputed in slot order.

``run()`` also applies the three tunable *opportunity-risk* gates from ``tlive/exec/risk.py``
(slippage / counterparty-p / priority-fee) as a pre-commit filter, matching the intended pipeline.
The token-safety gate (mint/freeze authority, transfer-hook) is a fixed RPC check that cannot be
rebuilt offline; it is orthogonal to the 12 tuned knobs and out of scope.

Usage:
    .venv/bin/python -m scripts.finetune.backtest --cache             # rebuild strict_replay.parquet
    .venv/bin/python -m scripts.finetune.backtest --vector '{"MIN_P":0.1}'
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict, deque
from pathlib import Path

import duckdb
import pandas as pd
from tlive.chain.swaps.model import Swap
from tlive.engine.allocator import Allocator
from tlive.metrics.exposure import exposure

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "screener.duckdb"
CACHE = ROOT / "data" / "exports" / "strict_replay.parquet"

BUDGET = 500_000_000                 # StrictVisibility.BUDGET_LAMPORTS, 0.5 SOL
MIN_PROFIT = 50_000                  # StrictVisibility.MIN_PROFIT_LAMPORTS
MIN_ROI = 0.01                       # StrictVisibility.MIN_ROI
FREQ_WINDOW = 300                    # StrictVisibility.FREQ_WINDOW
AGGREGATORS = ("JUP6LkbZ", "6Vo3245e", "term9YPb", "L2TExMFKdjpN")
MODEL_THRESHOLD = 0.02               # tier.MODEL_THRESHOLD
MIN_MESP_INTEREST = 10_000           # tier.MIN_MESP_INTEREST
GAIN_SANE = 1e12                     # lamports: the "no-limit u64" sentinel the codebase drops


def _swap(d: dict) -> Swap:
    opt = lambda k: int(d[k]) if d.get(k) is not None else None
    return Swap(signature=d.get("signature", ""), slot=int(d.get("slot", 0)), tx_index=int(d.get("tx_index", 0)),
                ev_index=int(d.get("ev_index", 0)), venue=d.get("venue", ""), pool=d.get("pool", ""),
                mint=d.get("mint", ""), user=d.get("signer", ""), is_buy=True, sol=int(d.get("sol", 0)),
                tok=0, x_pre=float(d.get("x_ref", 0)), y_pre=float(d.get("y_pre", 0)), fee_bps=float(d.get("fee_bps", 0)),
                kind=d.get("kind", "in"), amount=opt("amount"), limit=opt("limit"), ix_name=d.get("ix_name", ""))


def reconstruct() -> pd.DataFrame:
    """Rebuild the strict event stream (slot / tx order) with real settlement, cached to parquet."""
    con = duckdb.connect(str(DB), read_only=True)
    buys = con.execute("""select signature, ev_index, slot, tx_index, venue, pool, mint, signer, sol,
        x_ref, y_pre, fee_bps, mesp, phi, p, cu_price, jito_tip, dontfront, kind, amount, "limit", ix_name
        from swaps where is_buy order by slot, tx_index, ev_index""").df()
    prof = con.execute("""select v.signature, v.ev_index, max(s.profit) as profit from victims v
        left join sandwiches s on s.key = v.key group by 1, 2""").df()
    con.close()
    profit = dict(zip(zip(prof.signature, prof.ev_index), prof.profit))

    out, pool_q = [], defaultdict(deque)
    n_interesting = 0
    for r in buys.to_dict("records"):
        p = float(r.get("p") or 0.0)
        if not (p >= MODEL_THRESHOLD or (r.get("mesp") or 0) >= MIN_MESP_INTEREST):
            continue
        router = r.get("router") or ""
        if (r.get("jito_tip") or 0) > 0 or router[:8] in AGGREGATORS:
            continue
        n_interesting += 1
        try:
            e = exposure(_swap(r), capital=float(BUDGET))
        except (ArithmeticError, ValueError):
            continue
        if e.mesp < MIN_PROFIT or not 0 < e.front <= BUDGET or e.mesp / e.front < MIN_ROI:
            continue
        pool = r.get("pool") or ""
        q = pool_q[pool]
        q.append(int(r["slot"]))
        while q[0] <= int(r["slot"]) - FREQ_WINDOW:
            q.popleft()
        key = (r["signature"], int(r["ev_index"]))
        out.append({
            "signature": r["signature"], "ev_index": int(r["ev_index"]), "slot": int(r["slot"]),
            "tx_index": int(r["tx_index"]), "pool": pool, "mint": r.get("mint"), "p": p,
            "phi": r.get("phi"), "mesp": r.get("mesp"),
            "front_budget": int(e.front), "mesp_budget": int(e.mesp),
            "roi": round(e.mesp / e.front, 4), "pool_freq": len(q),
            "x_ref": r.get("x_ref"), "fee_bps": r.get("fee_bps"), "cu_price": r.get("cu_price"),
            "jito_tip": r.get("jito_tip"), "dontfront": r.get("dontfront"), "sol": r.get("sol"),
            "sandwiched": key in profit, "sw_profit": profit.get(key),
        })
    df = pd.DataFrame(out)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    print(f"interesting(visible) {n_interesting} -> strict {len(df)}; sandwiched {int(df.sandwiched.sum())}; -> {CACHE}")
    return df


def load_strict(force: bool = False) -> pd.DataFrame:
    """Strict rows (unbounded no-limit rows dropped), cached to ``CACHE``."""
    if force or not CACHE.exists():
        reconstruct()
    df = pd.read_parquet(CACHE)
    return df[df.mesp_budget <= GAIN_SANE].reset_index(drop=True)


def build_replay(df: pd.DataFrame) -> dict:
    """Pre-group the strict stream by slot (list, fastest replay) and build the settlement dict."""
    rows = df.sort_values(["slot", "tx_index"]).to_dict("records")
    by_slot = {}
    for r in rows:
        by_slot.setdefault(r["slot"], []).append(r)
    slots = sorted(by_slot.items())
    victims = {(r["signature"], int(r["ev_index"])): (r["signature"], "attacker", r["sw_profit"] or 0, 0, 0, 0)
               for r in rows if r["sandwiched"]}
    return {"slots": slots, "victims": victims, "n": len(rows)}

def run_mc(replay: dict, params: dict, seeds: int = 5) -> dict:
    """Mean of ``run`` over seeds: the honest expected P&L (no lucky-draw bias)."""
    rows = [run(replay, params, seed=s) for s in range(seeds)]
    out = {k: statistics.mean(r[k] for r in rows)
           for k in ("commits", "wins", "race_loss", "no_cp", "sandwiched", "completed",
                     "total_profit_sol", "max_drawdown", "sharpe")}
    return out


def split_replay(replay: dict, frac: float = 0.8) -> tuple[dict, dict]:
    """Split into an early (train) and late (holdout) time window along the slot axis."""
    slots = [s for s, _ in replay["slots"]]
    n_rows = sum(len(r) for _, r in replay["slots"])
    acc = 0
    cut = None
    for s, rows in replay["slots"]:
        acc += len(rows)
        if cut is None and acc >= frac * n_rows:
            cut = s
            break
    def sub(from_slot: int | None, to_slot: int | None) -> dict:
        slots_pick = [(s, r) for s, r in replay["slots"] if (from_slot is None or s > from_slot) and (to_slot is None or s <= to_slot)]
        rows = [r for _, grp in slots_pick for r in grp]
        victims = {(r["signature"], int(r["ev_index"])): (r["signature"], "a", r["sw_profit"] or 0, 0, 0, 0) for r in rows if r["sandwiched"]}
        return {"slots": slots_pick, "victims": victims, "n": len(rows)}
    train = sub(None, cut)
    hold = sub(cut, None)
    return train, hold


# Default opportunity-risk gate thresholds (mirror tlive/exec/risk.py defaults).
RISK_DEFAULTS = {"MAX_SLIPPAGE_FRAC": 0.05, "MIN_COUNTERPARTY_P": 0.02, "MAX_PRIORITY_FEE": 1_000_000}


def _risk_ok(r: dict, p: dict) -> bool:
    kappa = float(r.get("front_budget") or 0)
    depth = float(r.get("x_ref") or 0)
    slip = kappa / depth if depth > 0 else float("inf")
    if slip > p.get("MAX_SLIPPAGE_FRAC", RISK_DEFAULTS["MAX_SLIPPAGE_FRAC"]):
        return False
    if (r.get("p") or 0) < p.get("MIN_COUNTERPARTY_P", RISK_DEFAULTS["MIN_COUNTERPARTY_P"]):
        return False
    cu = r.get("cu_price")
    cu = int(cu) if cu is not None and not (isinstance(cu, float) and cu != cu) else 0
    if cu > p.get("MAX_PRIORITY_FEE", RISK_DEFAULTS["MAX_PRIORITY_FEE"]):
        return False
    return True


def run(replay: dict, params: dict, seed: int = 0) -> dict:
    """Replay the strict stream through risk gates + ``Allocator``; return the metrics."""
    alloc = Allocator(initial_sol=0.5, budget_sol=0.5, seed=seed)
    alloc.HISTORY_MAX = 10 ** 9                    # keep every gain for the Sharpe denominator
    for k, v in params.items():
        if k in ("MAX_SLIPPAGE_FRAC", "MIN_COUNTERPARTY_P", "MAX_PRIORITY_FEE"):
            continue                               # applied as the risk pre-filter, below
        if k == "CAPTURE_FRACTION" and v is not None:
            cf = float(v)
            alloc._capture_fraction = lambda pi: cf
        else:
            setattr(alloc, k, v)
    alloc.set_victims(replay["victims"])
    peak = 0.5
    max_dd = 0.0
    for slot, rows in replay["slots"]:
        kept = [r for r in rows if _risk_ok(r, params)]
        if kept:
            alloc.step(int(slot), kept)
        equity = alloc.wallet_sol() + alloc._sol(alloc.total_locked)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / 0.5)
    gains = [h["gain"] / 1e9 for h in alloc.history]
    n = len(gains)
    mean = sum(gains) / n if n else 0.0
    var = sum((g - mean) ** 2 for g in gains) / n if n else 0.0
    sharpe = mean / (var ** 0.5) if var > 0 else 0.0
    s = alloc.state()
    raced = alloc.n_wins + alloc.n_race_loss
    return {
        "commits": alloc.n_committed, "wins": alloc.n_wins, "race_loss": alloc.n_race_loss,
        "no_cp": alloc.n_no_counterparty, "sandwiched": alloc.n_sandwiched, "completed": alloc.n_completed,
        "total_profit_sol": s["total_profit"], "max_drawdown": max_dd, "sharpe": sharpe,
        "win_rate": alloc.n_wins / raced if raced else 0.0,
    }


class ExpectedAllocator(Allocator):
    """Same decisions, but resolution uses the win/loss *expectation* instead of an RNG draw.

    Lets the sweep evaluate any WIN_RATE / CAPTURE / TIP deterministically (no Monte-Carlo noise):
    a sandwiched position contributes ``WIN_RATE * (capture*pi - tip) + (1-WIN_RATE) * (-tip)``.
    """

    def _resolve(self, slot: int, events: list[dict]) -> None:
        due = [k for k, p in self.active.items() if p["unlock_slot"] <= slot]
        for key in due:
            pos = self.active.pop(key)
            pool = pos["pool"]
            self.active_pools[pool] -= 1
            if self.active_pools[pool] <= 0:
                del self.active_pools[pool]
            hit = self.victims.get(pos["key"])
            if hit is not None:
                self.n_sandwiched += 1
                self.sum_realized += hit[2]
                realized = int(pos["pi"] * self._capture_fraction(pos["pi"]))
                win_gain = max(0, realized - self.TIP_LAMPORTS)
                loss_gain = -self.TIP_LAMPORTS
                self.n_wins += self.WIN_RATE            # fractional: expectation, kept for the table
                self.n_race_loss += (1.0 - self.WIN_RATE)
                self._last_expected_gain = self.WIN_RATE * win_gain + (1.0 - self.WIN_RATE) * loss_gain
                gain = int(round(self._last_expected_gain))
            else:
                gain = -int(pos["kappa"] * self.ADVERSE)
            self.wallet += pos["kappa"] + gain
            self.total_locked -= pos["kappa"]
            self.total_profit += gain
            self.n_completed += 1
            self.n_no_counterparty += not hit


def run_expected(replay: dict, params: dict, seed: int = 0) -> dict:
    """Deterministic expected-P&L replay (no RNG). Returns the same metric keys as ``run``."""
    alloc = ExpectedAllocator(initial_sol=0.5, budget_sol=0.5, seed=seed)
    alloc.HISTORY_MAX = 10 ** 9
    for k, v in params.items():
        if k in ("MAX_SLIPPAGE_FRAC", "MIN_COUNTERPARTY_P", "MAX_PRIORITY_FEE"):
            continue
        if k == "CAPTURE_FRACTION" and v is not None:
            cf = float(v)
            alloc._capture_fraction = lambda pi: cf
        else:
            setattr(alloc, k, v)
    alloc.set_victims(replay["victims"])
    peak = 0.5
    max_dd = 0.0
    for slot, rows in replay["slots"]:
        kept = [r for r in rows if _risk_ok(r, params)]
        if kept:
            alloc.step(int(slot), kept)
        equity = alloc.wallet_sol() + alloc._sol(alloc.total_locked)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / 0.5)
    s = alloc.state()
    raced = alloc.n_wins + alloc.n_race_loss
    return {"commits": alloc.n_committed, "wins": alloc.n_wins, "race_loss": alloc.n_race_loss,
            "no_cp": alloc.n_no_counterparty, "sandwiched": alloc.n_sandwiched, "completed": alloc.n_completed,
            "total_profit_sol": s["total_profit"], "max_drawdown": max_dd, "wallet": s["wallet"],
            "win_rate": alloc.n_wins / raced if raced else 0.0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--vector", type=str, default="{}")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if args.cache:
        reconstruct()
        sys.exit(0)
    df = load_strict()
    replay = build_replay(df)
    params = json.loads(args.vector)
    from pprint import pprint
    pprint(run(replay, params, seed=args.seed))