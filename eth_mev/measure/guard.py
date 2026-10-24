"""Data-driven slippage-guard estimation for undecodable swaps.

The screener can read a victim's exact ``amountOutMinimum`` for ~25% of swaps (direct router /
aggregator / reverse-engineered private routers). For the rest it previously assumed a *fixed* 1%
slippage tolerance — which is wrong if real victims leave more or less room.

The fix: learn the empirical distribution of the **guard slack** from the decodable swaps, and use a
quantile of it as the default for undecodable ones. The learned quantity is

    guard_slack = actual_out / amountOutMinimum - 1     (≥ 0)

i.e. how much *additional* price movement a victim tolerates beyond what their own trade already
experienced. This is exactly the front-running room a sandwich needs, measured on the decodable
sub-population and applied to the rest.

The observed slacks persist to DuckDB so the distribution survives restarts (``db_path``), and the
percentile is tunable (``MEV_GUARD_PERCENTILE``) to trade conservative screening against tail-hunting.
"""
from __future__ import annotations

import time
from pathlib import Path


class GuardEstimator:
    """Running empirical distribution of victim guard slack, persisted to DuckDB."""

    def __init__(self, fallback: float = 0.01, percentile: float = 50.0, cap: int = 4000,
                 db_path: Path | str | None = None):
        self.fallback = fallback
        self.percentile = percentile          # 50 = median (default); lower = more conservative
        self.cap = cap
        self.db_path = Path(db_path) if db_path else None
        self.slacks: list[float] = []
        self.n_observed = 0
        self._pending: list[float] = []
        if self.db_path:
            self._load()

    # ---- persistence ----
    def _load(self) -> None:
        import duckdb
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(self.db_path))
            con.execute("create table if not exists guard_slacks (slack double, seen_ms bigint)")
            rows = con.execute("select slack from guard_slacks order by seen_ms").fetchall()
            con.close()
            self.slacks = [float(r[0]) for r in rows][-self.cap:]
            self.n_observed = len(self.slacks)
        except Exception:
            self.slacks = []

    def flush(self) -> None:
        if not self._pending or not self.db_path:
            return
        import duckdb
        try:
            now = int(time.time() * 1000)
            con = duckdb.connect(str(self.db_path))
            con.execute("create table if not exists guard_slacks (slack double, seen_ms bigint)")
            con.executemany("insert into guard_slacks values (?, ?)", [(s, now) for s in self._pending])
            con.close()
            self._pending = []
        except Exception:
            pass

    def close(self) -> None:
        self.flush()

    # ---- observation ----
    def observe(self, actual_out: int, amount_out_min: int) -> None:
        """Record one decodable swap's realized guard slack."""
        if not amount_out_min or actual_out < amount_out_min:
            return
        slack = actual_out / amount_out_min - 1.0
        if not (0.0 <= slack < 100.0):          # reject nonsense (unit mismatch / overflow)
            return
        self.slacks.append(slack)
        self.n_observed += 1
        self._pending.append(slack)
        if len(self.slacks) > self.cap:
            self.slacks = self.slacks[-self.cap:]
        if len(self._pending) >= 20:
            self.flush()

    # ---- estimate ----
    def default_slack(self) -> float:
        """The current estimate of a typical victim's guard slack (a percentile of observations)."""
        if not self.slacks:
            return self.fallback
        s = sorted(self.slacks)
        k = min(len(s) - 1, int(len(s) * self.percentile / 100.0))
        return s[k]

    def estimate_guard(self, actual_out: int) -> int:
        """Estimate ``amountOutMinimum`` for an undecodable swap whose actual output is known."""
        slack = self.default_slack()
        return int(actual_out / (1.0 + slack))

    def stats(self) -> dict:
        s = sorted(self.slacks)
        n = len(s)
        return {
            "n_observed": self.n_observed,
            "default_slack": self.default_slack(),
            "percentile": self.percentile,
            "p10": s[int(n * 0.10)] if n else None,
            "p25": s[int(n * 0.25)] if n else None,
            "p50": s[int(n * 0.50)] if n else None,
            "p75": s[int(n * 0.75)] if n else None,
            "p90": s[int(n * 0.90)] if n else None,
        }
