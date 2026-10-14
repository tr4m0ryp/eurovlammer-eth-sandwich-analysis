"""Delayed ground truth: which flagged swaps were actually sandwiched.

A sandwich's legs can span up to ``WINDOW_SLOTS`` slots and its back leg can trail, so a swap's
fate is settled only ``SETTLE`` slots after it. Each block the labeller re-runs the detector over
the last ``SPAN`` slots and emits exactly the sandwiches whose front leg sits in the settling slot,
so each one is reported once. Attackers pass the same H3 entity filter as offline, on running
session counts, or are known from the offline study (``bots.json``).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from dataclasses import fields
from pathlib import Path

from ..chain.swaps.model import Swap
from ..metrics.sandwich import WINDOW_SLOTS, Sandwich, find

SETTLE = WINDOW_SLOTS + 6
SPAN = 3 * WINDOW_SLOTS + 16
_FIELDS = [f.name for f in fields(Swap)]


class Labeler:
    def __init__(self, bots_path: Path | None = None):
        self.blocks: deque[tuple[int, list[Swap]]] = deque()
        self.wallet: dict[str, str] = {}
        self.known_bots = set(json.loads(bots_path.read_text())) if bots_path and bots_path.exists() else set()
        self.n_swaps: Counter[str] = Counter()
        self.att: dict[str, list[float]] = defaultdict(list)

    def is_bot(self, w: str) -> bool:
        if w in self.known_bots:
            return True
        ps = self.att.get(w, [])
        legs = 2 * len(ps)
        return len(ps) >= 3 and sum(p > 0 for p in ps) / len(ps) >= 0.6 and legs / max(self.n_swaps[w], 1) >= 0.25

    def bots(self) -> set[str]:
        return self.known_bots | {w for w in self.att if self.is_bot(w)}

    def push(self, slot: int, rows: list[dict]) -> tuple[int, list[Sandwich]]:
        """Add one block; returns (settled slot, sandwiches whose front leg starts there)."""
        sw = [Swap(**{k: r[k] for k in _FIELDS}) for r in rows]
        for r in rows:
            self.wallet[r["signature"]] = r["signer"]
            self.n_swaps[r["signer"]] += 1
        self.blocks.append((slot, sw))
        while self.blocks and self.blocks[0][0] < slot - SPAN:
            for s in self.blocks.popleft()[1]:
                self.wallet.pop(s.signature, None)
        settled = slot - SETTLE
        window = [s for _, b in self.blocks for s in b]
        out = [x for x in find(window, self.wallet) if x.front[0].slot == settled]
        for x in out:
            self.att[x.attacker].append(x.profit)
        return settled, out
