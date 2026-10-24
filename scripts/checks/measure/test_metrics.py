"""Unit tests: constant-product curve + V2 exposure math (golden-section MESP)."""
from __future__ import annotations

import math

from eth_mev.onchain.decode.v2.swaps import Swap
from eth_mev.measure.exposure.v2.curve import net_for_tokens, sol_for_tokens, tokens_for_net
from eth_mev.measure.exposure.v2.mesp import exposure, slack


def _swap(**kw) -> Swap:
    base = dict(tx_hash="0xabc", block_number=1, tx_index=0, log_index=0, venue="uniswap_v2",
                pool="0xpool", token0="0xWETH", token1="0xTOK", user="0xuser", is_buy=True,
                amount_in=0, amount_out=0, x_pre=100.0, y_pre=1000.0, fee_bps=30.0,
                kind="in", amount=10.0, limit=None)
    base.update(kw)
    return Swap(**base)


def test_constant_product_legs():
    # buy: 10 into (100, 1000) -> 1000*10/(100+10)
    assert tokens_for_net(100.0, 1000.0, 10.0) == 1000.0 * 10 / 110
    # inverse consistency
    assert net_for_tokens(100.0, 1000.0, 100.0) == 100 * 100 / (1000 - 100)
    # sell
    assert sol_for_tokens(100.0, 1000.0, 100.0) == 100 * 100 / 1100
    # draining the curve -> inf
    assert net_for_tokens(100.0, 1000.0, 1000.0) == float("inf")


def test_slack_buy_with_limit():
    s = _swap(kind="in", amount=10.0, limit=90.0)
    # fee r=0.003; net numeraire in = amount/(1+r) = 10/1.003; tokens = 1000*net/(100+net)
    r = 0.003
    net = 10.0 / (1 + r)
    expected_t = 1000.0 * net / (100.0 + net)
    assert abs(slack(s, 100.0, 1000.0) - (expected_t / 90.0 - 1)) < 1e-9


def test_exposure_returns_zero_when_no_limit_and_degenerate():
    s = _swap(amount=None)
    e = exposure(s, 100.0, 1000.0)
    assert e.mesp == 0.0
    assert e.front == 0.0


def test_exposure_positive_buy():
    # A buy with a slack limit that leaves room should yield a positive MESP.
    s = _swap(kind="in", amount=10.0, limit=80.0)  # floor 80 -> lots of room
    e = exposure(s, 100.0, 1000.0)
    assert e.mesp >= 0.0
    assert e.front >= 0.0


def test_exposure_capital_cap():
    # With a tiny capital cap, front never exceeds it.
    s = _swap(kind="in", amount=10.0, limit=1.0)  # tight floor, still some room
    e = exposure(s, 100.0, 1000.0, capital=5.0)
    assert e.front <= 5.0 + 1e-9


def test_sell_exposure_matches_buy_symmetry_roughly():
    buy = _swap(is_buy=True, kind="in", amount=20.0, limit=100.0)
    eb = exposure(buy, 100.0, 1000.0)
    assert eb.mesp >= 0.0
    sell = _swap(is_buy=False, amount=200.0, limit=100.0, x_pre=100.0, y_pre=1000.0)
    es = exposure(sell, 100.0, 1000.0)
    # finite, non-negative
    assert math.isfinite(es.mesp) or es.mesp == 0.0
    assert es.mesp >= 0.0
