"""Risk book: gas floor, attacker-state MESP for victims, gain shares, top-10% mark."""
from tlive.screen.risk import RiskBook


def row(sig, mesp, sol=1e9, front=5e8):
    return {"signature": sig, "ev_index": 0, "slot": 1, "sol": sol, "mesp": mesp, "front_opt": front}


def test_risk_rows_and_shares():
    rb = RiskBook()
    rb.add(1, [row("a", 5_000), row("b", 2e7), row("v", 0.0)] + [row(f"f{i}", 1e6 * (i + 1)) for i in range(60)])
    victims = {("v", 0): ("k", "ATT", 3e7, 1, 4e7, 2e8)}
    out = {r["signature"]: r for r in rb.settle(1, victims)}
    assert "a" not in out                                   # under the two-transaction gas floor
    assert out["v"]["status"] == "sandwiched" and out["v"]["mesp_best"] == 4e7
    assert abs(out["v"]["gain_pct"] - 0.04) < 1e-12 and abs(out["v"]["gain_roi"] - 0.2) < 1e-12
    assert abs(out["v"]["taken_pct"] - 0.03) < 1e-12
    assert out["b"]["status"] == "clean" and out["b"]["sw_profit"] is None
    top = [r for r in out.values() if r["top10"]]
    assert 5 <= len(top) <= 8 and not out["v"]["top10"] and out["f59"]["top10"]
