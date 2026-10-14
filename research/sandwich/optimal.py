"""The most optimal sandwich targets of the night: realistic, sandwichable and profitable risk buys.

Three gates, each measured rather than assumed (data/screener.duckdb):
  realistic     the theoretical gain is mapped to what attackers actually extract: an isotonic fit of
                realized profit on predicted gain over single-victim sandwiches (log-log). Rows whose
                math leaves its domain are out: unbounded spend, negative slack (the victim's limit is
                already broken at the reference state), front-run larger than the pool's SOL side.
  sandwichable  a buy whose own limit leaves room (MESP > 0, by construction of ``risk``).
  profitable    calibrated gain minus the attacker's landing cost (p90 of real attackers' two legs:
                base fees + priority fees + tips) is positive.
**Optimal** = the qualified rows in the top decile of calibrated net gain.

    .venv/bin/python -m research.sandwich.optimal
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "screener.duckdb"


def calibrate(con) -> tuple[IsotonicRegression, int]:
    pairs = con.execute("""select r.mesp_best, s.profit from risk r join victims v using (signature, ev_index)
        join sandwiches s on s.key = v.key
        where s.n_victims = 1 and r.status = 'sandwiched' and not r.gain_unbounded and r.mesp_best > 0""").fetchnumpy()
    pred, real = pairs["mesp_best"].astype(float), pairs["profit"].astype(float)
    iso = IsotonicRegression(increasing=True, out_of_bounds="clip")
    # log-log, profit floored at 1 lamport: a loss maps to the floor, so it pulls its bin down
    iso.fit(np.log10(pred), np.log10(np.maximum(real, 1.0)))
    return iso, len(pred)


def attacker_cost(con) -> float:
    return float(con.execute("""select quantile_cont(c, 0.9) from (select sandwich,
        sum(5000 + coalesce(priority_fee, 0) + coalesce(jito_tip, 0)) as c
        from research_labels where bot and role in ('front', 'back') group by 1)""").fetchone()[0])


def build() -> None:
    con = duckdb.connect(str(DB), config={"memory_limit": "8GB", "threads": 4})
    iso, n_cal = calibrate(con)
    cost = attacker_cost(con)
    grid = np.linspace(3, 10.5, 61)                       # log10 lamports, 1e3 .. 3e10
    con.execute("create or replace table optimal_calibration (log10_pred double, log10_real double)")
    con.executemany("insert into optimal_calibration values (?, ?)", [[g, float(iso.predict([g])[0])] for g in grid])
    con.execute("""create or replace temp table cand as select r.*, s.kind, s.ix_name, s.dontfront, s.phi,
        s.log_flow_rel, s.log_pool_n, s.log_wallet_n, s.log_pool_hist, s.priority_fee, s.amount, s."limit"
        from risk r left join (select distinct on (signature, ev_index) * from swaps) s using (signature, ev_index)""")
    df = con.execute("select signature, ev_index, mesp_best from cand").df()
    ok = df.mesp_best.fillna(0) > 0
    real = np.zeros(len(df))
    real[ok.to_numpy()] = 10 ** iso.predict(np.log10(df.mesp_best[ok].to_numpy(float)))
    df["gain_real"] = real
    con.register("calib", df[["signature", "ev_index", "gain_real"]])
    con.execute(f"""create or replace table optimal as select c.*, k.gain_real,
        k.gain_real - {cost} as net_real,
        not c.gain_unbounded and c.slack >= 0 and coalesce(c.front_best, 0) <= c.x as realistic,
        coalesce(c.mesp_best, 0) > 0 as sandwichable,
        k.gain_real - {cost} > 0 as profitable
        from cand c join calib k using (signature, ev_index)""")
    con.execute("alter table optimal add column qualified boolean")
    con.execute("update optimal set qualified = realistic and sandwichable and profitable")
    q90 = con.execute("select quantile_cont(net_real, 0.9) from optimal where qualified").fetchone()[0]
    con.execute("alter table optimal add column optimal boolean")
    con.execute(f"update optimal set optimal = qualified and net_real >= {q90}")
    print(f"calibration on {n_cal} single-victim sandwiches; attacker cost p90 {cost / 1e9:.6f} SOL")
    print(con.execute("""select count(*) risk, sum(realistic::int) realistic, sum(qualified::int) qualified,
        sum(optimal::int) optimal, round(avg((status='sandwiched')::int) filter (where optimal), 3) optimal_hit_rate,
        round(avg((status='sandwiched')::int) filter (where qualified and not optimal), 3) rest_hit_rate,
        round(sum(net_real) filter (where optimal) / 1e9, 2) optimal_net_sol,
        round(sum(sw_profit) filter (where optimal and status='sandwiched') / 1e9, 2) optimal_taken_sol
        from optimal""").df().to_string(index=False))
    print(f"optimal cutoff: calibrated net gain >= {q90 / 1e9:.5f} SOL")
    con.close()


if __name__ == "__main__":
    build()
