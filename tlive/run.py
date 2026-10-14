"""Live screener: follow confirmed blocks, score every pump.fun/PumpSwap swap, flag the ones a
sandwicher would pick, and settle each flag against the real sandwiches ``SETTLE`` slots later.

    set -a; . ~/projects/trenches/.env; set +a
    .venv/bin/python -m tlive.run            # page on http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import asyncio
import math
import time
import tomllib
from pathlib import Path

from .ingest.follow import Follower
from .ingest.rpc import default_pool
from .screen import Labeler, Scorer
from .serve import Hub, serve
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
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
                "bots": len([w for w in lab.att if lab.is_bot(w)]), "clock_rejects": f.clock_rejects,
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
    async with default_pool() as pool:
        f = Follower(pool)
        try:
            async for blk in f.blocks():
                slot = blk["slot"]
                st.slots += 1
                st.skipped += blk["skipped"]
                df = scorer.score(blk["swaps"], slot, lab.bots())
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
                settled, sands = lab.push(slot, blk["swaps"])
                for x in sands:
                    if not lab.is_bot(x.attacker):
                        continue
                    scorer.add_front(x.pool, x.front[0].slot)
                    keys = [(v.signature, v.ev_index) for v in x.victims]
                    for v in x.victims:
                        victims[(v.signature, v.ev_index)] = (x.key, x.attacker, x.profit, v.slot)
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
                    hub.sandwiches.append(srow)
                    hub.publish("sandwich", srow)
                done = [s for s in flagged_at if s <= settled]
                for s in done:
                    keys = flagged_at.pop(s)
                    st.settled_flags += len(keys)
                    st.flag_hits += sum(k in victims for k in keys)
                for s in [s for s in scored_at if s <= settled]:
                    st.calibrate(scored_at.pop(s), victims)
                for k in [k for k, v in victims.items() if v[3] < settled - 64]:
                    del victims[k]
                store.settle(settled, victims)
                hub.settle(settled, victims)
                hub.stats = st.view(f, scorer, lab, slot)
                hub.publish("stats", hub.stats)
        finally:
            f.exec.shutdown(cancel_futures=True)
            store.close()
            await runner.cleanup()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "live.toml")
    asyncio.run(main(tomllib.loads(ap.parse_args().config.read_text())))
