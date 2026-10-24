"""Unit tests: V3 allocator — no-flash-loan and flash-loan modes."""
from __future__ import annotations

from eth_mev.engine.allocation.v3 import V3Allocator, WEI


def _row(mesp=0.01 * WEI, front=0.045 * WEI, pool="0xpool", tx="0xabc", slack=0.05):
    return {"mesp": int(mesp), "front": int(front), "pool": pool, "tx_hash": tx,
            "roi": mesp / front, "slack": slack, "seen_ms": 0}


def _no_loan(bankroll=0.045):
    a = V3Allocator(bankroll_eth=bankroll)
    a.FLASH_LOAN_ETH = 0.0       # disable flash loan for these tests
    return a


def _loan(bankroll=0.09, loan=10.0):
    a = V3Allocator(bankroll_eth=bankroll)
    a.FLASH_LOAN_ETH = loan
    a.FLASH_FEE_BPS = 5
    return a


# ---- no-flash-loan mode (capital-constrained) ----
def test_no_loan_rejects_below_break_even():
    a = _no_loan()
    tiny = _row(mesp=int(0.00001 * WEI))
    assert a.decide(tiny) is False


def test_no_loan_rejects_front_over_capital():
    a = _no_loan()
    big = _row(mesp=int(0.1 * WEI), front=int(0.1 * WEI))
    assert a.decide(big) is False


def test_no_loan_commit_resolve():
    a = _no_loan()
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    a.BRIBE_WEI = 0
    row = _row(mesp=int(0.01 * WEI), front=int(0.01 * WEI))
    a.step(1, [row])
    assert a.n_committed == 1
    assert a.total_locked == int(0.01 * WEI)
    a.step(1 + a.RESOLVE_BLOCKS, [])
    assert a.n_resolved == 1
    cost = a.landing_cost_wei()
    expected = int(0.5 * 0.25 * int(0.01 * WEI)) - cost
    assert a.total_profit == expected


def test_no_loan_greedy_sorts_by_mesp():
    a = _no_loan()
    lo = _row(mesp=int(0.001 * WEI), front=int(0.03 * WEI), tx="0xlo")
    hi = _row(mesp=int(0.01 * WEI), front=int(0.03 * WEI), tx="0xhi")
    a.step(1, [lo, hi])
    assert a.n_committed == 1
    assert any(p["mesp"] == int(0.01 * WEI) for p in a.positions.values())


# ---- flash-loan mode (front is borrowed) ----
def test_flash_loan_front_not_deducted_from_wallet():
    a = _loan(bankroll=0.09, loan=10.0)
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    a.BRIBE_WEI = 0
    row = _row(mesp=int(1.0 * WEI), front=int(5.0 * WEI))   # 5 ETH front, 1 ETH mesp
    a.step(1, [row])
    assert a.n_committed == 1
    # the front is borrowed, so the bankroll is NOT reduced by 5 ETH
    assert a.wallet == a.bankroll
    assert a.total_locked == int(5.0 * WEI)


def test_flash_loan_fee_added_to_cost():
    a = _loan(bankroll=0.09, loan=10.0)
    a.FLASH_FEE_BPS = 5   # 0.05%
    front = int(10.0 * WEI)
    fee = a.flash_fee_wei(front)
    assert fee == int(10.0 * WEI * 5 / 10000) == int(0.005 * WEI)
    cost = a.landing_cost_wei(front)
    assert cost == a.gas_cost_wei + a.BRIBE_WEI + fee


def test_flash_loan_slack_floor():
    a = _loan(bankroll=0.09, loan=10.0)
    a.MIN_SLACK = 0.005
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    a.BRIBE_WEI = 0
    # tight slack (0.1%) -> rejected
    tight = _row(mesp=int(1.0 * WEI), front=int(5.0 * WEI), slack=0.001)
    assert a.decide(tight) is False
    # loose slack (5%) -> passes
    loose = _row(mesp=int(1.0 * WEI), front=int(5.0 * WEI), slack=0.05)
    assert a.decide(loose) is True


def test_flash_loan_resolve_returns_only_pnl():
    a = _loan(bankroll=0.09, loan=10.0)
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    a.BRIBE_WEI = 0
    a.FLASH_FEE_BPS = 0
    row = _row(mesp=int(1.0 * WEI), front=int(10.0 * WEI), slack=0.05)
    a.step(1, [row])
    assert a.wallet == a.bankroll          # front is loaned, not deducted
    a.step(1 + a.RESOLVE_BLOCKS, [])
    cost = a.landing_cost_wei(int(10.0 * WEI))
    expected = int(0.5 * 0.25 * int(1.0 * WEI)) - cost
    assert a.total_profit == expected
    # the loan is returned, so the wallet = bankroll + profit (no front refund)
    assert a.wallet == a.bankroll + expected


def test_flash_loan_cost_buffer_exhausted():
    a = _loan(bankroll=0.0001, loan=10.0)   # tiny buffer < cost
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    row = _row(mesp=int(1.0 * WEI), front=int(10.0 * WEI), slack=0.05)
    a.step(1, [row])
    assert a.n_committed == 0               # can't afford the cost


def test_risk_appetite_scales_threshold():
    a = _loan(bankroll=0.09, loan=10.0)
    a.WIN_RATE = 0.5
    a.CAPTURE_FRACTION = 0.25
    a.BRIBE_WEI = 0
    a.FLASH_FEE_BPS = 0
    full = a.commit_threshold_wei(int(10.0 * WEI))
    assert full == 8 * a.landing_cost_wei(int(10.0 * WEI))
    a.RISK_APPETITE = 4.0
    assert a.commit_threshold_wei(int(10.0 * WEI)) == full // 4
