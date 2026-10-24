"""Deterministic replay backtest: strict events from the persisted DuckDB fed through ``Allocator``.

Ground truth is the run's own settlement — the ``victims``/``sandwiches`` tables (which victims a
real sandwich landed on) — so a run is reproducible and does not touch the live stream. The strict
universe is rebuilt from ``pending_swaps`` by replaying the strict filter's math:
``interesting`` → budget-capped ``exposure(swap, capital=budget)`` → ``mesp >= MIN_PROFIT`` and
``roi >= MIN_ROI``. The per-pool ``pool_freq`` is recomputed in block order.

Rewritten from ``tlive/scripts/finetune/backtest.py``: only ``reconstruct()`` + settlement change
(the ETH remap), the replay/sweep skeleton is unchanged.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from pathlib import Path

import duckdb
import pandas as pd

from eth_mev.measure.exposure.v2.mesp import _swap_from_row, exposure

BUDGET = int(1e18)          # 1 ETH
MIN_PROFIT = 10 ** 16       # 0.01 ETH
MIN_ROI = 0.01
FREQ_BLOCKS = 12
MODEL_THRESHOLD = 0.02
MIN_MESP_INTEREST = 10 ** 16
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
