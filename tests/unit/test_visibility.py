"""The shared tier rule matches the original four-branch rule, and the hub honours topics."""
import asyncio
import itertools

from tlive.screen.visibility import LiveVisibility, tier
from tlive.screen.visibility.strict import FREQ_WINDOW, StrictVisibility
from tlive.serve.hub import Hub


def _old_tier(lag: int, tipped: bool, routed: bool) -> str:
    if lag <= 3 and not tipped and not routed:
        return "visible"
    if lag <= 8 and (tipped or routed):
        return "marginal"
    if lag <= 3 and (tipped or routed):
        return "marginal"
    if lag <= 8 and not tipped and not routed:
        return "marginal"
    return "invisible"


def test_tier_matches_original_rule():
    for lag, tipped, routed in itertools.product(range(12), (False, True), (False, True)):
        r = {"jito_tip": 1000 if tipped else 0, "router": "JUP6LkbZabc" if routed else ""}
        assert tier(r, lag) == _old_tier(lag, tipped, routed)


def test_live_counts_only_interesting_buys():
    v = LiveVisibility()
    rows = [{"is_buy": True, "p": 0.5}, {"is_buy": True, "p": 0.0, "mesp": 0}, {"is_buy": False, "p": 0.9}]
    out = v.add(1, 2, rows)
    assert len(out) == 1 and v.stats() == {"n": 1, "visible": 1, "marginal": 0, "invisible": 0}


def test_strict_prunes_idle_pools():
    sv = StrictVisibility()
    sv.pool_slots["old"] = __import__("collections").deque([10])
    sv._freq("new", 10 + FREQ_WINDOW + 5)
    sv._prune(10 + FREQ_WINDOW + 5)
    assert list(sv.pool_slots) == ["new"]


def test_hub_topic_filter_and_stats_throttle():
    async def run():
        hub = Hub()
        only_strict = hub.subscribe(frozenset({"strict", "stats"}))
        everything = hub.subscribe(None)
        hub.add_flag({"signature": "a", "ev_index": 0, "slot": 1, "status": "pending"})
        hub.add_strict([{"signature": "b"}])
        hub.set_stats({"slot": 1})
        hub.set_stats({"slot": 2})          # inside the throttle window: stored, not sent
        assert only_strict.qsize() == 2 and everything.qsize() == 3
        assert hub.stats == {"slot": 2}
    asyncio.run(run())
