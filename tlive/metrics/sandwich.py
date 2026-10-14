"""Ground-truth sandwich labels from one block's true transaction order.

A sandwich on a pool is a front-run F, one or more victims V, and a back-run B, all in one slot
with ``F < V < B`` by in-block index, where
  * F and V trade the same side, B the opposite side;
  * F and B belong to one attacker: the same signer or event user, OR (attackers split legs over
    wallets) B unwinds what F took, ``|tok_B - tok_F| <= 2% * tok_F``;
  * a victim is any same-side swap between F and B by a signer that is neither F's nor B's.
The nearest qualifying B is taken, so nested round trips do not merge.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..chain.swaps.model import Swap

TOK_MATCH = 0.02


@dataclass
class Sandwich:
    slot: int
    pool: str
    venue: str
    front: Swap
    back: Swap
    attacker: str
    victims: list[Swap] = field(default_factory=list)
    linked_by: str = ""

    @property
    def profit(self) -> float:
        """Attacker's gross SOL gain over the round trip, pool fees included, tips excluded."""
        scale = self.back.tok / self.front.tok if self.front.tok else 1.0
        if self.front.is_buy:
            return self.back.gross - self.front.gross * scale
        return self.front.gross * scale - self.back.gross


def _link(f: Swap, b: Swap, signer: dict[str, str]) -> str:
    if signer.get(f.signature) == signer.get(b.signature):
        return "signer"
    if f.user == b.user:
        return "user"
    if f.tok and abs(b.tok - f.tok) <= TOK_MATCH * f.tok:
        return "amount"
    return ""


def find(swaps: list[Swap], signer: dict[str, str]) -> list[Sandwich]:
    """Sandwiches in one block. ``signer`` maps signature -> fee payer."""
    by_pool: dict[str, list[Swap]] = defaultdict(list)
    for s in swaps:
        by_pool[s.pool].append(s)
    out: list[Sandwich] = []
    for pool, seq in by_pool.items():
        if len(seq) < 3:
            continue
        seq.sort(key=lambda s: (s.tx_index, s.ev_index))
        used: set[int] = set()
        for i, f in enumerate(seq):
            if i in used:
                continue
            fs = signer.get(f.signature)
            for k in range(i + 2, len(seq)):
                b = seq[k]
                if k in used or b.is_buy == f.is_buy or b.tx_index == f.tx_index:
                    continue
                how = _link(f, b, signer)
                if not how:
                    continue
                bs = signer.get(b.signature)
                victims = [v for j, v in enumerate(seq[i + 1:k], i + 1)
                           if j not in used and v.is_buy == f.is_buy and v.tx_index not in (f.tx_index, b.tx_index)
                           and signer.get(v.signature) not in (fs, bs) and v.user not in (f.user, b.user)]
                if victims:
                    used.update({i, k})
                    out.append(Sandwich(f.slot, pool, f.venue, f, b, fs or "", victims, how))
                break
    return out
