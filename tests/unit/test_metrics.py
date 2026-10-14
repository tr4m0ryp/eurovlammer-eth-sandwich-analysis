"""Exposure math and cross-slot sandwich detection on synthetic swaps."""
from tlive.chain.swaps.model import Swap
from tlive.metrics.exposure import exposure
from tlive.metrics.features import base_features
from tlive.metrics.sandwich import find

X, Y = 100e9, 1e15


def swap(sig, slot, idx, is_buy, sol, tok, user, fee=30.0, kind="in", amount=None, limit=None, x=X, y=Y, pool="P"):
    return Swap(sig, slot, idx, 0, "pumpswap", pool, "M", user, is_buy, int(sol), int(tok), x, y, fee,
                kind, amount, limit, "buy" if is_buy else "sell")


def test_no_slack_no_profit():
    tok = Y * 5e9 / (X + 5e9)
    s = swap("v", 1, 0, True, 5e9, tok, "V", kind="in", amount=int(5e9 * 1.003), limit=int(tok * 0.9999))
    assert exposure(s).mesp == 0.0


def test_slack_creates_profit_and_fee_destroys_it():
    s = swap("v", 1, 0, True, 5e9, 0, "V", kind="in", amount=int(5e9 * 1.003), limit=1)
    low = exposure(s).mesp
    s.fee_bps = 250.0
    s.amount = int(5e9 * 1.025)
    assert low > 1e7 and exposure(s).mesp < low


def test_phi_is_fee_normalised():
    import pandas as pd
    df = pd.DataFrame([{"x_ref": X, "sol": 3e8, "fee_bps": 120.0, "is_buy": True, "slack": 0.1, "mesp": 0.0,
                        "kind": "in", "venue": "pumpswap", "router": "", "dontfront": False, "cu_price": 0, "jito_tip": 0},
                       {"x_ref": 5e9, "sol": 3e8, "fee_bps": 30.0, "is_buy": True, "slack": 0.1, "mesp": 0.0,
                        "kind": "in", "venue": "pumpswap", "router": "", "dontfront": False, "cu_price": 0, "jito_tip": 0}])
    phi = base_features(df)["phi"].to_numpy()
    assert phi[0] < 1 < phi[1]


def test_cross_slot_sandwich_detected():
    f = swap("f", 10, 5, True, 1e9, 1e13, "A")
    v = swap("v", 11, 2, True, 2e9, 2e13, "V")
    b = swap("b", 13, 0, False, 1.05e9, 0.995e13, "A")
    noise = swap("n", 12, 1, False, 1e8, 1e12, "N")
    out = find([f, v, noise, b], {"f": "A", "v": "V", "b": "A", "n": "N"})
    assert len(out) == 1 and [s.signature for s in out[0].victims] == ["v"]


def test_unmatched_back_leg_is_not_a_sandwich():
    f = swap("f", 10, 5, True, 1e9, 1e13, "A")
    v = swap("v", 11, 2, True, 2e9, 2e13, "V")
    b = swap("b", 12, 0, False, 1e8, 1e12, "A")          # resells 10%: below the 50% floor
    assert find([f, v, b], {"f": "A", "v": "V", "b": "A"}) == []


def test_back_leg_outside_window_is_not_a_sandwich():
    f = swap("f", 10, 5, True, 1e9, 1e13, "A")
    v = swap("v", 11, 2, True, 2e9, 2e13, "V")
    b = swap("b", 30, 0, False, 1.05e9, 1e13, "A")
    assert find([f, v, b], {"f": "A", "v": "V", "b": "A"}) == []
