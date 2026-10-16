"""Sandwich exposure + ground-truth detection (inclusion-order, mev-inspect style).

Unlike the Solana (Heimbach-style, multi-slot) detector, Ethereum sandwiches settle *within one
block*: a victim is sandwiched when the attacker's swap appears both before and after it in the
same block's transaction order, with positive atomic profit. This module labels those pairs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..chain.swaps.model import Swap


@dataclass
class Sandwich:
    pool: str
    attacker: str
    front: list[Swap] = field(default_factory=list)
    back: list[Swap] = field(default_factory=list)
    victims: list[Swap] = field(default_factory=list)
    block_number: int | None = None
    venue: str = ""

    @property
    def key(self) -> str:
        f = self.front[0]
        return f"{f.block_number}:{f.tx_index}:{f.log_index}"

    @property
    def profit(self) -> float:
        """Attacker's net token0 gain over the round trip (fees included, gas/tips excluded).

        Direction-agnostic: if the front bought token0 (net0>0) and the back sold it, profit is the
        token0 taken out minus what was put in across both legs; the sign flips for the reverse.
        For a WETH-paired pool where token0=WETH this is the ETH profit.
        """
        def net0(s: Swap) -> int:
            raw = getattr(s, "raw", None) or {}
            return int(raw.get("a0o") or 0) - int(raw.get("a0i") or 0)
        f = sum(net0(s) for s in self.front)
        b = sum(net0(s) for s in self.back)
        return float(f + b) if self.front and _dir(self.front[0]) > 0 else float(-(f + b))


def _tx(s: Swap) -> tuple[int, int]:
    return (s.tx_index, s.log_index)


def _dir(s: Swap) -> int:
    """Signed token0 flow: +1 if token0 is bought (net token0 out), -1 if token0 is sold.

    Faithful to the emitted ``Swap`` event regardless of which token is the numeraire — a front-run
    and its back-run have opposite sign. Falls back to ``is_buy`` (token1-out) when raw legs absent.
    """
    raw = getattr(s, "raw", None) or {}
    if "a0o" in raw:
        net0 = int(raw.get("a0o") or 0) - int(raw.get("a0i") or 0)
        if net0 > 0:
            return 1
        if net0 < 0:
            return -1
    return 1 if s.is_buy else -1


def find_sandwiches(swaps: Iterable[Swap], tx_owner: dict[str, str]) -> list[Sandwich]:
    """Find front+back pairs around a victim within one block, mev-inspect style.

    ``tx_owner`` maps tx_hash -> sender (the tx origin, filled from the block's transactions). A
    victim is a same-direction swap, by a *different* wallet, strictly between an attacker's two
    opposite-direction legs in the same pool. Direction is the *signed token0 flow* (opposite for
    the two legs), so detection is independent of which token is token0.
    """
    by_pool: dict[str, list[Swap]] = {}
    for s in swaps:
        if s.block_number is None:
            continue
        by_pool.setdefault(s.pool, []).append(s)
    out: list[Sandwich] = []
    for pool, seq in by_pool.items():
        if len(seq) < 3:
            continue
        seq.sort(key=_tx)
        mine: dict[str, list[Swap]] = {}
        for s in seq:
            owner = tx_owner.get(s.tx_hash, s.user) or s.user
            mine.setdefault(owner, []).append(s)
        for w, ss in mine.items():
            if len(ss) < 2:
                continue
            if len({_dir(s) for s in ss}) < 2:   # needs both directions
                continue
            # front = consecutive same-direction, back = the opposite-direction swap later.
            for i in range(len(ss) - 1):
                f, b = ss[i], ss[i + 1]
                if _dir(f) == _dir(b):
                    continue
                front_dir = _dir(f)
                # notional round-trip check on token0 (direction-agnostic)
                front_tok = (int(f.raw.get("a0o") or 0) if front_dir > 0 else int(f.raw.get("a0i") or 0)) or f.amount_out
                back_tok = (int(b.raw.get("a0i") or 0) if front_dir > 0 else int(b.raw.get("a0o") or 0)) or b.amount_in
                if front_tok and back_tok and not (0.5 * front_tok <= back_tok <= 1.01 * front_tok):
                    continue
                victims = [v for v in seq if _tx(f) < _tx(v) < _tx(b)
                           and _dir(v) == front_dir
                           and (tx_owner.get(v.tx_hash, v.user) or v.user) != w]
                if victims:
                    out.append(Sandwich(pool, w, [f], [b], victims, f.block_number, f.venue))
    return out


def top_heavy(sands: list[Sandwich]) -> dict:
    """Sanity summary: sandwich count and profit share by attacker (should be top-heavy)."""
    by: dict[str, list[Sandwich]] = {}
    for s in sands:
        by.setdefault(s.attacker, []).append(s)
    per = {w: sum(s.profit for s in ss) for w, ss in by.items()}
    if not per:
        return {"n": 0, "n_attackers": 0, "share_top1": 0.0}
    total = sum(per.values()) or 1.0
    top = sorted(per.values(), reverse=True)
    return {"n": len(sands), "n_attackers": len(per), "share_top1": top[0] / total}
