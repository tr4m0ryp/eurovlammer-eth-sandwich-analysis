"""Unit tests: allocator decision + settlement lifecycle, risk gate, and live-path gating."""
from __future__ import annotations

import pytest

from eth_mev.engine.allocator import Allocator, WEI
from eth_mev.exec.risk import event_risks, gate
from eth_mev.exec.sign import load_account
from eth_mev.exec.flashbots import FlashbotsRelay, LiveGateError, live_gates_met


def _row(block=1, tx_hash="0xabc", p=0.5, pi=0.1 * WEI, position=0.01 * WEI, pool="0xpool",
         pool_freq=0, x_ref=10 * WEI, priority_fee=1_000_000_000):
    return {"block": block, "tx_hash": tx_hash, "p": p, "expected_profit_wei": int(pi),
            "mesp_budget": int(pi), "position_wei": int(position), "front_budget": int(position),
            "roi": pi / position, "pool": pool, "pool_freq": pool_freq, "x_ref": x_ref,
            "priority_fee": priority_fee, "base_fee": 20_000_000_000}


def test_allocator_commits_and_wins():
    a = Allocator(initial_eth=1.0, budget_eth=1.0, seed=0)
    row = _row(pi=0.1 * WEI)
    # settlement says it got sandwiched -> win (WIN_RATE 0.5 draw, seeded)
    a.set_settlement({(1, "0xabc"): ("0xabc", "attacker", 0.05 * WEI, 1)})
    events = a.step(1, [row])
    assert any(e["type"] == "commit" for e in events)
    assert a.n_committed == 1
    # resolve at block 1 + RESOLVE_BLOCKS
    a.step(1 + a.RESOLVE_BLOCKS, [])
    assert a.n_completed == 1
    assert a.n_sandwiched == 1
    assert a.n_wins + a.n_outbid == 1


def test_allocator_no_sandwich_unwinds_at_adverse():
    a = Allocator(initial_eth=1.0, budget_eth=1.0, seed=0)
    row = _row(pi=0.1 * WEI)
    # no settlement -> no_sandwich
    a.set_settlement({})
    a.step(1, [row])
    a.step(1 + a.RESOLVE_BLOCKS, [])
    assert a.n_completed == 1
    assert a.n_no_sandwich == 1
    assert a.total_profit < 0


def test_allocator_respects_bankroll_cap():
    a = Allocator(initial_eth=0.5, budget_eth=0.5, seed=0)
    row = _row(position=0.6 * WEI)
    a.set_settlement({})
    a.step(1, [row])
    assert a.n_committed == 0  # position > bankroll, rejected


def test_allocator_min_p_gate():
    a = Allocator(initial_eth=1.0, budget_eth=1.0, seed=0)
    a.MIN_P = 0.9
    row = _row(p=0.5)
    a.set_settlement({})
    a.step(1, [row])
    assert a.n_committed == 0


def test_risk_gate():
    risks = event_risks({"position_wei": 0.01 * WEI, "x_ref": 10 * WEI, "p": 0.5,
                         "priority_fee": 1_000_000_000})
    assert risks["slippage_frac"] == pytest.approx(0.001)
    ok, reasons = gate({"safe": True, "risks": []}, risks)
    assert ok and not reasons


def test_risk_gate_rejects_slippage():
    risks = event_risks({"position_wei": 0.5 * WEI, "x_ref": WEI, "p": 0.5,
                         "priority_fee": 1_000_000_000})
    ok, reasons = gate({"safe": True, "risks": []}, risks)
    assert not ok and "slippage" in reasons


def test_live_gates_default_off():
    ok, missing = live_gates_met()
    assert not ok
    assert any("LIVE" in m for m in missing)


def test_flashbots_refuses_without_live_gate():
    from eth_mev.ingest.rpc import Rpc
    relay = FlashbotsRelay(Rpc("http://localhost:1"))
    with pytest.raises(LiveGateError):
        relay._assert_live()


def test_sign_refuses_without_key(monkeypatch):
    monkeypatch.delenv("ETH_BOT_KEY", raising=False)
    monkeypatch.delenv("ETH_BOT_PRIVKEY", raising=False)
    with pytest.raises(RuntimeError):
        load_account()


def test_sign_loads_explicit_key():
    from eth_account import Account
    acct = Account.create()
    loaded = load_account("0x" + acct.key.hex())
    assert loaded.address == acct.address


def test_fee_heads_no_float_string_bug(monkeypatch):
    """The dry-run screener's fee resolver must not feed ``str(float)`` to ``int()``."""
    import sys
    sys.path.insert(0, ".")
    import run
    monkeypatch.setenv("MEV_BASE_FEE_WEI", "20000000000")
    monkeypatch.setenv("MEV_PRIORITY_FEE_WEI", "1000000000")
    fees = run._resolve_fee_heads()
    assert fees["base_fee"] == 20_000_000_000
    assert fees["priority_fee"] == 1_000_000_000
    # and with no env vars set (the bug was in the default path)
    monkeypatch.delenv("MEV_BASE_FEE_WEI", raising=False)
    monkeypatch.delenv("MEV_PRIORITY_FEE_WEI", raising=False)
    fees2 = run._resolve_fee_heads()
    assert fees2["base_fee"] == 20_000_000_000
    assert fees2["priority_fee"] == 1_000_000_000
