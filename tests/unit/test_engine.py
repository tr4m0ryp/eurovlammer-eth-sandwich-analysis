"""The allocation engine keeps wealth accounting exact and never breaches the exposure cap."""
import random

from tlive.engine import Allocator


def _row(i, slot, kappa, roi, pool="p"):
    return {"signature": f"s{i}", "ev_index": 0, "slot": slot, "seen_ms": 0, "pool": pool,
            "front_budget": kappa, "mesp_budget": int(kappa * roi), "roi": roi,
            "pool_freq": 1, "p": 0.8, "fee_bps": 30}


def test_wealth_accounting_and_exposure_cap():
    rng = random.Random(0)
    alloc = Allocator(seed=0)
    seen = []
    for slot in range(60):
        batch = []
        for _ in range(rng.randint(0, 3)):
            kappa = rng.choice((4_000_000, 10_000_000, 40_000_000, 120_000_000))
            roi = rng.uniform(0.01, 0.12)
            batch.append(_row(len(seen), slot, kappa, roi, pool=f"p{len(seen) % 4}"))
            seen.append(batch[-1])
        for ev in alloc.step(slot, batch):
            assert ev["type"] in ("commit", "resolve", "stats")
        st = alloc.state()
        # W = C + B = C0 + profit, so free + locked must track initial + realised profit
        assert abs((st["wallet"] + st["total_locked"]) - (0.5 + st["total_profit"])) < 1e-6
        assert st["total_locked"] <= 0.5 + 1e-9
        assert st["wallet"] >= 0
        assert st["n_active"] == len(st["positions"])
    assert alloc.n_completed > 0
    assert len(alloc.history) == alloc.n_completed


def test_lock_resolves_by_unlock_slot():
    alloc = Allocator(seed=1)
    row = _row(0, 10, 40_000_000, 0.05)
    alloc.step(10, [row])
    # the position is committed (high ROI, fits the scarce cap) and resolves after RESOLVE_DELAY
    assert alloc.n_committed == 1 and len(alloc.active) == 1
    alloc.step(10 + Allocator.RESOLVE_DELAY, [])
    assert len(alloc.active) == 0
    assert alloc.n_completed == 1
