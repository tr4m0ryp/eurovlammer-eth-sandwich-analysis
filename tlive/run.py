"""Live screener: follow confirmed blocks, score every pump.fun/PumpSwap swap, flag the ones a
sandwicher would pick, and settle each flag against the real sandwiches ``SETTLE`` slots later.

    set -a; . ~/projects/trenches/.env; set +a
    .venv/bin/python -m tlive.run            # page on http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import signal
import time
import tomllib
from pathlib import Path

import aiohttp
from solders.keypair import Keypair

from .engine import Allocator
from .exec import SandwichExecutor
from .exec.keypair import load_keypair
from .exec.risk import event_risks, gate
from .exec.safety import check_tokens
from .ingest.follow import Follower
from .ingest.rpc import default_pool
from .metrics.exposure import exposure
from .screen import Labeler, Scorer
from .screen.risk import KEEP as RISK_KEEP, RiskBook
from .screen.visibility import LiveVisibility, StrictVisibility
from .serve import Hub, serve
from .serve.artifact import snapshot
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
PUSH_EVERY_S = 60           # artifact snapshot cadence (one database write per push)
EDGES = [0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7]


def _clean(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None if math.isnan(v) else 1e9
    return v.item() if hasattr(v, "item") else v


class Stats:
    def __init__(self):
        self.t0 = time.time()
        self.slots = self.skipped = self.swaps = self.flags = 0
        self.settled_flags = self.flag_hits = self.victims = self.victims_flagged = self.sandwiches = 0
        self.profit = 0.0
        self.exec_commits = self.exec_rejects = self.exec_errors = 0
        self.risk_safe = 0
        self.risk_rejects: dict[str, int] = {}
        self.cal_n = [0] * len(EDGES)
        self.cal_hit = [0] * len(EDGES)

    def calibrate(self, scored: list[tuple[tuple, float]], victims: dict) -> None:
        for key, p in scored:
            b = max(i for i, e in enumerate(EDGES) if p >= e)
            self.cal_n[b] += 1
            self.cal_hit[b] += key in victims

    def view(self, f: Follower, scorer: Scorer, lab: Labeler, slot: int) -> dict:
        return {"slot": slot, "tip": f.tip, "lag": f.lag_slots, "uptime_s": int(time.time() - self.t0),
                "slots": self.slots, "skipped": self.skipped, "swaps": self.swaps, "flags": self.flags,
                "settled_flags": self.settled_flags, "flag_hits": self.flag_hits, "victims": self.victims,
                "victims_flagged": self.victims_flagged, "sandwiches": self.sandwiches,
                "profit_sol": self.profit / 1e9, "threshold": scorer.threshold,
                "exec_commits": self.exec_commits, "exec_rejects": self.exec_rejects, "exec_errors": self.exec_errors,
                "risk_safe": self.risk_safe, "risk_rejects": self.risk_rejects,
                "bots": len(lab.bots()) - len(lab.known_bots), "clock_rejects": f.clock_rejects,
                "model_meta": {k: v for k, v in scorer.meta.items() if not isinstance(v, (dict, list))},
                "calibration": [{"lo": e, "n": n, "hits": h} for e, n, h in zip(EDGES, self.cal_n, self.cal_hit)]}


async def main(cfg: dict) -> None:
    run_id = time.strftime("live_%Y%m%d_%H%M%S", time.gmtime())
    model = ROOT / cfg["model"]["path"]
    scorer, lab, hub, st = Scorer(model), Labeler(ROOT / cfg["model"]["bots"]), Hub(), Stats()
    store = Store(ROOT / cfg["run"]["out_dir"] / run_id, {"run_id": run_id, "model": scorer.meta})
    runner = await serve(hub, cfg["run"]["host"], cfg["run"]["port"])
    print(f"{run_id}: http://{cfg['run']['host']}:{cfg['run']['port']}", flush=True)
    flagged_at: dict[int, set] = {}
    scored_at: dict[int, list] = {}
    victims: dict[tuple[str, int], tuple] = {}      # every victim seen; a front can precede its victim
    risk = RiskBook()
    vis = LiveVisibility()
    sv = StrictVisibility()
    alloc = Allocator()
    alloc.set_victims(victims)          # settle the wallet against the real sandwich feed
    # real executor (dry-run): signs with the configured trader keypair, else a throwaway for sim.
    # Opt-in via TLIVE_EXEC=1: its per-event RPC calls are throttled off by default so the screener
    # stays on the tip while it collects ground truth for the profitability assessment.
    exec_enabled = os.environ.get("TLIVE_EXEC") == "1"
    rpc_url = os.environ.get("ALCHEMY_RPC_URL", "") or os.environ.get("HELIUS_RPC_URL", "")
    trader = load_keypair("TLIVE_TRADER_KEYPAIR") if os.environ.get("TLIVE_TRADER_KEYPAIR") else Keypair()
    ex = SandwichExecutor(rpc_url, trader, live=False,
                          rpc_fallback=os.environ.get("HELIUS_RPC_URL", "") or None)
    last_push = 0.0
    push_path = store.dir / "push" / "snapshot.json"
    async with default_pool() as pool:
        f = Follower(pool, start_lag=1)     # follow 1 slot behind the tip (was 4): ~1.2 s less latency
        ex_session = aiohttp.ClientSession()
        try:
            async for blk in f.blocks():
                slot = blk["slot"]
                st.slots += 1
                st.skipped += blk["skipped"]
                df = scorer.score(blk["swaps"], slot, lab.bots())
                strict_rows = []
                if len(df):
                    st.swaps += len(df)
                    store.swaps(df, slot)
                    now = int(time.time() * 1000)
                    rows = []
                    for r in df[df.flag].to_dict("records"):
                        row = {k: _clean(r.get(k)) for k in ("signature", "ev_index", "slot", "block_time", "venue",
                               "pool", "mint", "signer", "router", "is_buy", "sol", "x_ref", "fee_bps", "slack",
                               "mesp", "phi", "p", "cu_price", "jito_tip", "dontfront")}
                        row.update(status="pending", attacker=None, sw_profit=None, seen_ms=now)
                        rows.append(row)
                        hub.add_flag(row)
                    store.flagged(rows)
                    st.flags += len(rows)
                    flagged_at[slot] = {(r["signature"], r["ev_index"]) for r in rows}
                    scored_at[slot] = list(zip(zip(df.signature, df.ev_index), df.p.astype(float)))
                    buys = df[df.is_buy][[c for c in RISK_KEEP if c in df.columns]]
                    risk.add(slot, [{k: _clean(v) for k, v in r.items()} for r in buys.to_dict("records")])
                    # the strict book recomputes exposure, so it needs the raw buy (kind, amount, limit)
                    buys_all = [{k: _clean(v) for k, v in r.items()} for r in df[df.is_buy].to_dict("records")]
                    vis.add(slot, f.lag_slots, buys_all)          # tier counts only
                    strict_rows = sv.add(slot, f.lag_slots, buys_all)
                    hub.add_strict(strict_rows)
                for ev in alloc.step(slot, strict_rows):          # resolve + decide every block
                    hub.publish("wallet", ev)
                hub.set_wallet(alloc.state())
                # risk recognition (always on): token safety (batched) + opportunity risks
                if strict_rows:
                    safety_map = await check_tokens(ex_session, rpc_url, [r.get("mint") or "" for r in strict_rows])
                for row in strict_rows:
                    try:
                        safety = safety_map.get(row.get("mint") or "") if strict_rows else {}
                        ok, reasons = gate(safety, event_risks(row))
                        if not ok:
                            for reason in reasons:
                                st.risk_rejects[reason] = st.risk_rejects.get(reason, 0) + 1
                            continue
                        st.risk_safe += 1
                        if exec_enabled:                          # real executor (dry-run): front leg
                            d = await ex.front_run(ex_session, row)
                            if d["decision"] == "commit":
                                st.exec_commits += 1
                                print(f"exec commit {row.get('pool','')[:8]} κ={d['kappa']/1e9:.3f} "
                                      f"sim={d.get('sim_err')}", flush=True)
                            else:
                                st.exec_rejects += 1
                    except Exception as e:
                        st.exec_errors += 1
                        print(f"exec error {e!r}", flush=True)
                settled, sands = lab.push(slot, blk["swaps"])
                for x in sands:
                    if not lab.is_bot(x.attacker):
                        continue
                    scorer.add_front(x.pool, x.front[0].slot)
                    keys = [(v.signature, v.ev_index) for v in x.victims]
                    for v in x.victims:          # mesp_ref: priced at the pool state before the front leg
                        e = exposure(v, x.front[0].x_pre, x.front[0].y_pre)
                        victims[(v.signature, v.ev_index)] = (x.key, x.attacker, x.profit, v.slot, e.mesp, e.front)
                    fl = [k in flagged_at.get(v.slot, ()) for k, v in zip(keys, x.victims)]
                    st.sandwiches += 1
                    st.victims += len(keys)
                    st.victims_flagged += sum(fl)
                    st.profit += x.profit
                    srow = {"key": x.key, "slot": x.front[0].slot, "pool": x.pool, "venue": x.venue,
                            "attacker": x.attacker, "profit": x.profit, "n_victims": len(keys), "n_flagged": sum(fl),
                            "victim_sol": sum(v.sol for v in x.victims)}
                    store.sandwich(list(srow.values()) + [int(time.time() * 1000)],
                                   [[x.key, v.signature, v.ev_index, v.slot, v.sol, None, f_] for v, f_ in zip(x.victims, fl)])
                    hub.add_sandwich(srow)
                done = [s for s in flagged_at if s <= settled]
                for s in done:
                    keys = flagged_at.pop(s)
                    st.settled_flags += len(keys)
                    st.flag_hits += sum(k in victims for k in keys)
                for s in [s for s in scored_at if s <= settled]:
                    st.calibrate(scored_at.pop(s), victims)
                for k in [k for k, v in victims.items() if v[3] < settled - 64]:
                    del victims[k]
                settled_risk = risk.settle(settled, victims)
                store.risk(settled_risk)
                hub.add_risk(settled_risk)
                store.settle(settled, victims)
                hub.settle(settled, victims)
                hub.set_stats(st.view(f, scorer, lab, slot) | {"risk": risk.stats(), "vis": vis.stats(),
                                                               "strict_vis": sv.stats()})
                if time.time() - last_push >= PUSH_EVERY_S:
                    last_push = time.time()
                    snapshot(hub, push_path)
        finally:
            await ex_session.close()
            f.exec.shutdown(cancel_futures=True)
            store.close()
            await runner.cleanup()


async def _until_term(coro) -> None:
    """Turn SIGTERM into a cancel, so ``main``'s cleanup runs and the decode workers are shut down
    (a bare SIGTERM kills the process and orphans every forkserver worker)."""
    task = asyncio.ensure_future(coro)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "live.toml")
    asyncio.run(_until_term(main(tomllib.loads(ap.parse_args().config.read_text()))))
