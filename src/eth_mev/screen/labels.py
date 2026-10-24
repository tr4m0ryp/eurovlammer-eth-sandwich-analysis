"""Settlement labeller: mev-inspect style, marks which pending swaps actually got sandwiched.

Runs per confirmed block: decode the block's swaps (from receipts) in tx-index order, find the
front+back sandwich pairs (``metrics/sandwich.py``), and emit the settlement feed the allocator keyes
off — ``(block, tx_hash) -> (key, attacker, profit, block_no)``. Also marks clean swaps.
"""
from __future__ import annotations


from ..chain.v2.swaps import Swap
from ..metrics.sandwich import Sandwich, find_sandwiches


class Labeller:
    def __init__(self):
        self.settlement: dict = {}     # (block, tx_hash) -> (key, attacker, profit, block_no)
        self.sandwiches: list[Sandwich] = []
        self.n_victims = 0

    def block(self, block_number: int, swaps: list[Swap], tx_owner: dict[str, str]) -> dict:
        """Label one block. Returns a settle dict for this block plus the sandwiches found."""
        sands = find_sandwiches(swaps, tx_owner)
        self.sandwiches.extend(sands)
        block_settle: dict = {}
        for s in sands:
            # each victim in this sandwich is a (block, tx_hash) key the allocator can look up.
            for v in s.victims:
                key = (block_number, v.tx_hash)
                block_settle[key] = (s.key, s.attacker, s.profit, block_number)
                self.n_victims += 1
        self.settlement.update(block_settle)
        return {"settle": block_settle, "sandwiches": sands}
