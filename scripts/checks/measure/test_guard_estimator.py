"""Unit tests: data-driven guard-slack estimator."""
from __future__ import annotations

from eth_mev.measure.guard import GuardEstimator


def test_fallback_before_observations():
    g = GuardEstimator(fallback=0.01)
    assert g.default_slack() == 0.01


def test_observes_and_uses_median():
    g = GuardEstimator(fallback=0.01)
    # observed guard slacks: 1%, 2%, 5% → median 2%
    g.observe(101, 100)   # slack 1%
    g.observe(102, 100)   # slack 2%
    g.observe(105, 100)   # slack 5%
    assert abs(g.default_slack() - 0.02) < 1e-9
    assert g.n_observed == 3


def test_estimate_guard_uses_learned_slack():
    g = GuardEstimator(fallback=0.01)
    g.observe(102, 100)   # 2% slack
    # actual out 1000 → guard estimate = 1000 / 1.02 ≈ 980
    assert g.estimate_guard(1000) == int(1000 / 1.02)


def test_rejects_nonsense_slack():
    g = GuardEstimator(fallback=0.01)
    g.observe(10 ** 30, 1)   # absurd slack, rejected
    assert g.n_observed == 0
    assert g.default_slack() == 0.01


def test_percentile():
    g = GuardEstimator(fallback=0.01, percentile=90.0)
    g.observe(101, 100)  # 1%
    g.observe(102, 100)  # 2%
    g.observe(110, 100)  # 10%
    # p90 of [1%, 2%, 10%] ≈ 10%
    assert abs(g.default_slack() - 0.10) < 1e-9


def test_persistence_roundtrip(tmp_path):
    db = tmp_path / "guard.duckdb"
    g = GuardEstimator(fallback=0.01, db_path=db)
    g.observe(101, 100)   # 1% slack
    g.observe(102, 100)   # 2% slack
    g.observe(105, 100)   # 5% slack
    g.flush()
    # reload from db
    g2 = GuardEstimator(fallback=0.01, db_path=db)
    assert g2.n_observed == 3
    assert abs(g2.default_slack() - 0.02) < 1e-9   # median of [1%, 2%, 5%]


def test_persistence_batches_flush(tmp_path):
    db = tmp_path / "guard.duckdb"
    g = GuardEstimator(fallback=0.01, db_path=db)
    for i in range(25):
        g.observe(100 + i, 100)   # slacks 0..24%, triggers flush at 20
    assert len(g._pending) == 5    # 20 flushed, 5 still pending
    g.flush()
    g2 = GuardEstimator(fallback=0.01, db_path=db)
    assert g2.n_observed == 25
