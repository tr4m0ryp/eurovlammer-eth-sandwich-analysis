"""V3 sandwich screener hub: shared state between the pipeline and the web handlers.

Same shape as the Solana ``tlive/serve/hub.py`` (topic-filtered SSE, snapshot, throttled stats), but
the rows are V3 opportunities: pool, tick, fee tier, sqrt price, victim guard, MESP and front.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque

RECENT = 600
QUEUE_CAP = 2000
STATS_EVERY_S = 1.0


class Hub:
    def __init__(self):
        self.strict: deque[dict] = deque(maxlen=RECENT)
        self.sandwiches: deque[dict] = deque(maxlen=200)
        self.wallet: dict = {}
        self.stats: dict = {}
        self.subs: dict[asyncio.Queue, frozenset[str] | None] = {}
        self._stats_at = 0.0

    def subscribe(self, topics: frozenset[str] | None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs[q] = topics
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.pop(q, None)

    def close(self) -> None:
        for q in self.subs:
            q.put_nowait(None)

    def publish(self, kind: str, data) -> None:
        targets = [q for q, t in self.subs.items() if (t is None or kind in t) and q.qsize() < QUEUE_CAP]
        if not targets:
            return
        msg = json.dumps({"t": kind, "d": data}).encode()
        for q in targets:
            q.put_nowait(msg)

    def set_stats(self, stats: dict) -> None:
        self.stats = stats
        now = time.monotonic()
        if now - self._stats_at >= STATS_EVERY_S:
            self._stats_at = now
            self.publish("stats", stats)

    def add_strict(self, rows: list[dict]) -> None:
        if rows:
            self.strict.extend(rows)
            self.publish("strict", rows)

    def add_sandwich(self, row: dict) -> None:
        self.sandwiches.append(row)
        self.publish("sandwich", row)

    def set_wallet(self, state: dict) -> None:
        self.wallet = state
        self.publish("wallet", state)
