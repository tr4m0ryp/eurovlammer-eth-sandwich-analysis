"""Ground-truth sandwich labels, wide and cross-slot, from true chain order.

Follows the H1/H2 heuristics of Heimbach, Solmaz, Oz and Ferreira Torres (2026, "No Place to
Hide", arXiv 2609.28115), with Solana's parameters: since the 2025 validator delistings ~96% of
Solana sandwiches span several slots, so a same-slot detector misses almost all of them.

H1 (structure). A candidate is a wallet trading both directions on one pool. Its swaps are scanned
in chain order; a run of same-direction swaps is a front leg, the next opposite run a back leg.
The back leg must resell what the front bought, ``0.5 * tok_F <= tok_B <= 1.01 * tok_F`` (the
lower bound tolerates exits cashed out over several transactions), and start within
``WINDOW_SLOTS`` (two leader rotations) of the front. Every swap on the pool in the front
direction, by another wallet, strictly between the legs and not itself a leg, is a victim.
H2 (one role). A swap that is a leg of any candidate is never a victim; legs are used once.
The entity filter (H3) is applied by the caller over many sandwiches (``entity_filter``).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..chain.swaps.model import Swap

WINDOW_SLOTS = 8
LO, HI = 0.5, 1.01


@dataclass
class Sandwich:
    pool: str
    venue: str
    attacker: str
    front: list[Swap]
    back: list[Swap]
    victims: list[Swap] = field(default_factory=list)

    @property
    def key(self) -> str:
        f = self.front[0]
        return f"{f.slot}:{f.tx_index}:{f.ev_index}"

    @property
    def profit(self) -> float:
        """Attacker's SOL gain over the round trip, pool fees included, tips and gas excluded.
        The front cost is scaled to the tokens actually resold."""
        f_tok = sum(s.tok for s in self.front)
        b_tok = sum(s.tok for s in self.back)
        f_sol = sum(s.gross for s in self.front)
        b_sol = sum(s.gross for s in self.back)
        scale = min(b_tok / f_tok, 1.0) if f_tok else 1.0
        return (b_sol - f_sol * scale) if self.front[0].is_buy else (f_sol * scale - b_sol)


def _order(s: Swap) -> tuple[int, int, int]:
    return (s.slot, s.tx_index, s.ev_index)


def _legs(seq: list[Swap]) -> list[tuple[list[Swap], list[Swap]]]:
    """(front, back) leg pairs of one wallet on one pool, its swaps in chain order."""
    runs: list[list[Swap]] = []
    for s in seq:
        if runs and runs[-1][0].is_buy == s.is_buy:
            runs[-1].append(s)
        else:
            runs.append([s])
    out = []
    i = 0
    while i + 1 < len(runs):
        f, b = runs[i], runs[i + 1]
        f_tok, b_tok = sum(s.tok for s in f), sum(s.tok for s in b)
        if b[0].slot - f[-1].slot <= WINDOW_SLOTS and LO * f_tok <= b_tok <= HI * f_tok:
            out.append((f, b))
            i += 2
        else:
            i += 1
    return out


def find(swaps: list[Swap], wallet: dict[str, str]) -> list[Sandwich]:
    """All sandwiches in a chain-ordered window of swaps. ``wallet`` maps signature -> signer."""
    by_pool: dict[str, list[Swap]] = defaultdict(list)
    for s in swaps:
        by_pool[s.pool].append(s)
    out: list[Sandwich] = []
    for pool, seq in by_pool.items():
        if len(seq) < 3:
            continue
        seq.sort(key=_order)
        mine: dict[str, list[Swap]] = defaultdict(list)
        for s in seq:
            mine[wallet.get(s.signature, s.user)].append(s)
        cands = []
        for w, ss in mine.items():
            if len(ss) >= 2 and any(s.is_buy for s in ss) and not all(s.is_buy for s in ss):
                cands += [(w, f, b) for f, b in _legs(ss)]
        if not cands:
            continue
        leg_ids = {id(s) for _, f, b in cands for s in f + b}
        for w, f, b in cands:
            lo, hi = _order(f[-1]), _order(b[0])
            vict = [v for v in seq if lo < _order(v) < hi and v.is_buy == f[0].is_buy
                    and id(v) not in leg_ids and wallet.get(v.signature, v.user) != w
                    and v.tx_index != f[-1].tx_index]
            if vict:
                out.append(Sandwich(pool, f[0].venue, w, f, b, vict))
    return out


def entity_filter(sands: list[Sandwich], n_swaps: dict[str, int], min_count: int = 3,
                  min_profitable: float = 0.6, min_density: float = 0.25) -> set[str]:
    """H3: attackers whose detected round trips look like a business, not coincidence.

    ``n_swaps`` counts every swap per wallet in the same window (density denominator). The
    paper's floor of 100 sandwiches fits a three-year window; a few hours needs a lower one."""
    by: dict[str, list[Sandwich]] = defaultdict(list)
    for s in sands:
        by[s.attacker].append(s)
    keep = set()
    for w, ss in by.items():
        legs = sum(len(s.front) + len(s.back) for s in ss)
        prof = sum(s.profit > 0 for s in ss) / len(ss)
        if len(ss) >= min_count and prof >= min_profitable and legs / max(n_swaps.get(w, legs), 1) >= min_density:
            keep.add(w)
    return keep
