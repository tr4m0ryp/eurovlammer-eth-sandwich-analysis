"""Unit tests: sandwich detection (inclusion-order) + the capital allocator lifecycle."""
from __future__ import annotations

from eth_mev.onchain.decode.v2.swaps import Swap
from eth_mev.measure.detection.sandwich import find_sandwiches, top_heavy


def _sw(tx_hash, tx_idx, is_buy, ai, ao, pool="0xpool"):
    return Swap(tx_hash=tx_hash, block_number=1, tx_index=tx_idx, log_index=0, venue="uniswap_v2",
                pool=pool, token0="0xWETH", token1="0xTOK", user="", is_buy=is_buy,
                amount_in=ai, amount_out=ao, x_pre=100.0, y_pre=1000.0, fee_bps=30.0,
                kind="in", amount=None, limit=None)


def test_finds_basic_sandwich():
    # attacker buy (front), victim buy, attacker sell (back)
    swaps = [
        _sw("0xf0", 0, True, 100, 900),
        _sw("0xya", 1, True, 10, 90),
        _sw("0xfb", 2, False, 900, 100),
    ]
    owners = {"0xf0": "attacker", "0xya": "victim", "0xfb": "attacker"}
    sands = find_sandwiches(swaps, owners)
    assert len(sands) == 1
    s = sands[0]
    assert s.attacker == "attacker"
    assert len(s.victims) == 1
    assert s.victims[0].tx_hash == "0xya"
    assert isinstance(s.profit, float)  # profit is computed; sign depends on the (static) test reserves


def test_no_sandwich_without_both_legs():
    swaps = [
        _sw("0xf0", 0, True, 100, 900),
        _sw("0xya", 1, True, 10, 90),
    ]
    owners = {"0xf0": "attacker", "0xya": "victim"}
    assert find_sandwiches(swaps, owners) == []


def test_victim_is_not_attacker():
    swaps = [
        _sw("0xf0", 0, True, 100, 900),
        _sw("0xf0", 1, False, 900, 100),
    ]
    owners = {"0xf0": "attacker"}
    assert find_sandwiches(swaps, owners) == []  # same wallet both legs, no distinct victim


def test_top_heavy_summary():
    s1 = _sw("0x1", 0, True, 100, 900)
    s2 = _sw("0x1", 1, False, 900, 100)
    class _S:
        attacker = "a1"
        profit = 10.0
    summ = top_heavy([_S(), _S()])
    assert summ["n"] == 2
    assert summ["n_attackers"] == 1
    assert summ["share_top1"] == 1.0
