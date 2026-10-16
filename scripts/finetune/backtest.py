"""Deterministic replay backtest: strict events from the persisted DuckDB fed through ``Allocator``.

Ground truth is the run's own settlement — the ``victims``/``sandwiches`` tables (which victims a
real sandwich landed on) — so a run is reproducible and does not touch the live stream. The strict
universe is rebuilt from ``pending_swaps`` by replaying the strict filter's math:
``interesting`` → budget-capped ``exposure(swap, capital=budget)`` → ``mesp >= MIN_PROFIT`` and
``roi >= MIN_ROI``. The per-pool ``pool_freq`` is recomputed in block order.

Rewritten from ``tlive/scripts/finetune/backtest.py``: only ``reconstruct()`` + settlement change
(the ETH remap), the replay/sweep skeleton is unchanged.

Usage:
    .venv/bin/python -m scripts.finetune.backtest --cache --db data/live_.../live.duckdb
    .venv/bin/python -m scripts.finetune.backtest --vector '{"MIN_P":0.1}' --db ...
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict, deque
from pathlib import Path

import duckdb
import pandas as pd

from eth_mev.engine.allocator import Allocator
from eth_mev.metrics.exposure import _swap_from_row, exposure

ROOT = Path(__file__).resolve().parents[2]
WEI = 1_000_000_000_000_000_000

BUDGET = int(1e18)          # 1 ETH
MIN_PROFIT = 10_000_000     # 0.01 ETH
MIN_ROI = 0.01
FREQ_BLOCKS = 12
MODEL_THRESHOLD = 0.02
MIN_MESP_INTEREST = 10_000_000
GAIN_SANE = 1e30            # wei sentinel for unbounded no-limit rows


def reconstruct(db_path: Path, cache: Path) -> pd.DataFrame:
    """Rebuild the strict event stream (block / tx order) with real settlement, cached to parquet."""
    con = duckdb.connect(str(db_path), read_only=True)
    # Ground truth lives in included_swaps (confirmed blocks, decoded Swap events) joined to
    # victims/sandwiches for the settled status. The collector captures the victim's calldata guard
    # (kind/amount/limit = amountOutMin) where it is a direct V2 router call.
    has_guard = "limit" in [c[0] for c in con.execute("describe included_swaps").fetchall()]
    guard_cols = ', kind, amount, "limit", deadline' if has_guard else ', null as kind, null as amount, null as "limit", null as deadline'
    swaps = con.execute(f"""select s.tx_hash, s.log_index, s.block_number as block, s.tx_index, s.pool,
        s.venue, s.token0, s.token1, s.user, s.is_buy, s.amount_in, s.amount_out, s.x_pre, s.y_pre,
        s.fee_bps{guard_cols},
        coalesce(v.status, 'clean') as status, v.attacker, v.sw_profit
        from included_swaps s
        left join victims v on v.tx_hash = s.tx_hash and v.log_index = s.log_index
        order by s.block_number, s.tx_index, s.log_index""").df()
    con.close()

    out, pool_q = [], defaultdict(deque)
    n_interesting = 0
    for r in swaps.to_dict("records"):
        # No model score in ground truth; use a slack-derived heuristic ``p`` (mirrors the scorer's
        # fallback) so the allocator's MIN_P gate is meaningful offline.
        slack_v = float(r.get("slack") or 0.0) if r.get("slack") is not None else 0.0
        p = min(max(0.02 + 0.10 * (math.log10(1.0 + slack_v) if slack_v > 0 else 0.0), 0.0), 0.40)
        if p < MODEL_THRESHOLD and (r.get("mesp") or 0) < MIN_MESP_INTEREST:
            # Ground-truth swaps carry no precomputed mesp; compute exposure directly below.
            pass
        n_interesting += 1
        try:
            e = exposure(_swap_from_row(r), x=float(r.get("x_pre") or 0.0), y=float(r.get("y_pre") or 0.0), capital=float(BUDGET))
        except (ArithmeticError, ValueError, TypeError):
            continue
        if e.mesp < MIN_PROFIT or e.front <= 0 or e.mesp / e.front < MIN_ROI:
            continue
        pool = r.get("pool") or ""
        q = pool_q[pool]
        q.append(int(r["block"]))
        while q[0] <= int(r["block"]) - FREQ_BLOCKS:
            q.popleft()
        out.append({
            "tx_hash": r["tx_hash"], "log_index": int(r["log_index"]), "block": int(r["block"]),
            "tx_index": int(r["tx_index"]), "pool": pool, "p": p,
            "slack": e.slack, "mesp": e.mesp, "position_wei": int(e.front), "expected_profit_wei": int(e.mesp),
            "front_budget": int(e.front), "mesp_budget": int(e.mesp),
            "roi": round(e.mesp / e.front, 4), "pool_freq": len(q),
            "x_ref": r.get("x_pre"), "fee_bps": r.get("fee_bps"),
            "priority_fee": 0, "base_fee": 0,
            "sandwiched": (r.get("status") == "sandwiched"),
            "sw_profit": r.get("sw_profit"),
        })
    df = pd.DataFrame(out)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    print(f"included {len(swaps)} -> interesting {n_interesting} -> strict {len(df)}; "
          f"sandwiched {int(df.sandwiched.sum()) if not df.empty else 0}; -> {cache}")
    return df


def load_strict(db_path: Path, cache: Path, force: bool = False) -> pd.DataFrame:
    if force or not cache.exists():
        reconstruct(db_path, cache)
    df = pd.read_parquet(cache)
    return df[df.mesp_budget <= GAIN_SANE].reset_index(drop=True)


def build_replay(df: pd.DataFrame) -> dict:
    rows = df.sort_values(["block", "tx_index"]).to_dict("records")
    by_block = {}
    for r in rows:
        by_block.setdefault(r["block"], []).append(r)
    blocks = sorted(by_block.items())
    settlement = {(r["block"], r["tx_hash"]): (r["tx_hash"], "attacker", r["sw_profit"] or 0, r["block"])
                  for r in rows if r["sandwiched"]}
    return {"blocks": blocks, "settlement": settlement, "n": len(rows)}


def split_replay(replay: dict, frac: float = 0.8) -> tuple[dict, dict]:
    n_rows = sum(len(r) for _, r in replay["blocks"])
    acc = 0
    cut = None
    for b, rows in replay["blocks"]:
        acc += len(rows)
        if cut is None and acc >= frac * n_rows:
            cut = b
            break

    def sub(from_b, to_b):
        pick = [(b, r) for b, r in replay["blocks"] if (from_b is None or b > from_b) and (to_b is None or b <= to_b)]
        rows = [r for _, grp in pick for r in grp]
        settlement = {(r["block"], r["tx_hash"]): (r["tx_hash"], "a", r["sw_profit"] or 0, r["block"])
                      for r in rows if r["sandwiched"]}
        return {"blocks": pick, "settlement": settlement, "n": len(rows)}

    return sub(None, cut), sub(cut, None)


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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--vector", type=str, default="{}")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--db", type=str, default=None)
    args = ap.parse_args()
    db = Path(args.db) if args.db else next((ROOT / "data").glob("live_*/live.duckdb"), None)
    if db is None:
        print("no DuckDB found; pass --db", file=sys.stderr)
        sys.exit(1)
    cache = db.parent / "strict_replay.parquet"
    if args.cache:
        reconstruct(db, cache)
        sys.exit(0)
    df = load_strict(db, cache)
    replay = build_replay(df)
    params = json.loads(args.vector)
    from pprint import pprint
    pprint(run(replay, params, seed=args.seed))
