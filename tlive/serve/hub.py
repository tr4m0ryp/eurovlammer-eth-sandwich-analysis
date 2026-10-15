"""The only state shared between the pipeline and the web handlers.

The pipeline writes rows and stats in; every open stream subscribes to a set of topics (``flag``,
``settle``, ``sandwich``, ``risk``, ``strict``, ``stats``) and only receives those. A message is
serialized once per publish, and a slow client drops events rather than holding the pipeline.
``stats`` change on every block (2-5 a second); streams get them at most every ``STATS_EVERY_S``.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

import orjson

RECENT = 600
QUEUE_CAP = 2000
STATS_EVERY_S = 1.0


class Hub:
    def __init__(self):
        self.flags: deque[dict] = deque(maxlen=RECENT)
        self.sandwiches: deque[dict] = deque(maxlen=200)
        self.risk: deque[dict] = deque(maxlen=RECENT)
        self.visible_optimal: deque[dict] = deque(maxlen=200)
        self.strict_optimal: deque[dict] = deque(maxlen=200)
        self.stats: dict = {}
        self.by_key: dict[tuple[str, int], dict] = {}
        self.subs: dict[asyncio.Queue, frozenset[str] | None] = {}
        self._stats_at = 0.0

    def subscribe(self, topics: frozenset[str] | None) -> asyncio.Queue:
        """A queue receiving ``topics`` (all of them when None)."""
        q: asyncio.Queue = asyncio.Queue()
        self.subs[q] = topics
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.pop(q, None)

    def close(self) -> None:
        """End every open stream; a stream that never ends holds the server's cleanup forever."""
        for q in self.subs:
            q.put_nowait(None)

    def publish(self, kind: str, data) -> None:
        targets = [q for q, t in self.subs.items() if (t is None or kind in t) and q.qsize() < QUEUE_CAP]
        if not targets:
            return
        msg = orjson.dumps({"t": kind, "d": data}, option=orjson.OPT_SERIALIZE_NUMPY)
        for q in targets:
            q.put_nowait(msg)

    def set_stats(self, stats: dict) -> None:
        self.stats = stats
        now = time.monotonic()
        if now - self._stats_at >= STATS_EVERY_S:
            self._stats_at = now
            self.publish("stats", stats)

    def add_flag(self, row: dict) -> None:
        if len(self.flags) == self.flags.maxlen:
            old = self.flags[0]
            self.by_key.pop((old["signature"], old["ev_index"]), None)
        self.flags.append(row)
        self.by_key[(row["signature"], row["ev_index"])] = row
        self.publish("flag", row)

    def add_risk(self, rows: list[dict]) -> None:
        if rows:
            self.risk.extend(rows)
            self.publish("risk", rows)

    def add_sandwich(self, row: dict) -> None:
        self.sandwiches.append(row)
        self.publish("sandwich", row)

    def add_strict(self, rows: list[dict]) -> None:
        """One batch per block, so a busy block costs the page one message, not one per row."""
        if rows:
            self.strict_optimal.extend(rows)
            self.publish("strict", rows)

    def settle(self, slot: int, victims: dict) -> None:
        changed = []
        for row in self.flags:
            if row["status"] != "pending" or row["slot"] > slot:
                continue
            hit = victims.get((row["signature"], row["ev_index"]))
            if hit:
                row.update(status="sandwiched", attacker=hit[1], sw_profit=hit[2])
            else:
                row["status"] = "clean"
            changed.append({k: row[k] for k in ("signature", "ev_index", "status", "attacker", "sw_profit")})
        if changed:
            self.publish("settle", changed)
