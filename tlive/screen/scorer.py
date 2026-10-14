"""Score each new swap with the trained model, with features identical to the backtest's.

Base features come from the same vectorised code. Context features (same-side pool flow and pool
trade count over the last ``CTX_SLOTS``, the signer's trade count over the last ``WALLET_SLOTS``)
are kept incrementally in per-pool and per-signer deques, so a block costs O(its swaps) instead
of a recompute over history; ``tests/unit/test_scorer.py`` pins parity with ``context_features``.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..metrics.features import (CTX_SLOTS, FEATURES, POOL_HIST_SLOTS, SETTLE_SLOTS, WALLET_SLOTS,
                                base_features)


class Context:
    """Rolling pool flow and wallet activity, fed strictly in chain order."""

    def __init__(self):
        self.pool: dict[str, deque] = defaultdict(deque)      # (slot, signed lamports)
        self.wallet: dict[str, deque] = defaultdict(deque)    # slot
        self.last_gc = 0

    def features(self, df: pd.DataFrame) -> pd.DataFrame:
        flow, pool_n, wallet_n = [], [], []
        for slot, pool, sol, buy, signer in zip(df["slot"], df["pool"], df["sol"], df["is_buy"], df["signer"]):
            q = self.pool[pool]
            while q and q[0][0] < slot - CTX_SLOTS:
                q.popleft()
            net = sum(v for _, v in q)
            flow.append(net if buy else -net)
            pool_n.append(len(q))
            q.append((slot, sol if buy else -sol))
            w = self.wallet[signer]
            while w and w[0] < slot - WALLET_SLOTS:
                w.popleft()
            wallet_n.append(len(w))
            w.append(slot)
        x = df["x_ref"].astype(float).clip(lower=1.0).to_numpy()
        out = pd.DataFrame(index=df.index)
        out["log_flow_rel"] = np.log10(np.abs(np.array(flow, dtype=float)) / x + 1e-6)
        out["log_pool_n"] = np.log10(np.array(pool_n, dtype=float) + 1)
        out["log_wallet_n"] = np.log10(np.array(wallet_n, dtype=float) + 1)
        return out

    def gc(self, slot: int) -> None:
        """Drop pools and wallets with nothing left in their windows (every ~1000 slots)."""
        if slot - self.last_gc < 1000:
            return
        self.last_gc = slot
        for d, horizon in ((self.pool, CTX_SLOTS), (self.wallet, WALLET_SLOTS)):
            for k in [k for k, q in d.items() if not q or (q[-1][0] if isinstance(q[-1], tuple) else q[-1]) < slot - horizon]:
                del d[k]


class Scorer:
    def __init__(self, model_path: Path):
        self.model = lgb.Booster(model_file=str(model_path))
        self.meta = json.loads(model_path.with_suffix(".json").read_text())
        self.threshold = float(self.meta["threshold"])
        self.ctx = Context()
        self.fronts: dict[str, deque] = defaultdict(deque)      # pool -> settled front-leg slots

    def add_front(self, pool: str, slot: int) -> None:
        """A settled sandwich on ``pool`` (from the labeller, SETTLE_SLOTS behind the tip)."""
        self.fronts[pool].append(slot)

    def _pool_hist(self, df: pd.DataFrame) -> np.ndarray:
        out = np.zeros(len(df))
        for i, (pool, slot) in enumerate(zip(df["pool"], df["slot"])):
            q = self.fronts.get(pool)
            if q:
                while q and q[0] < slot - POOL_HIST_SLOTS:
                    q.popleft()
                out[i] = sum(1 for f in q if f < slot - SETTLE_SLOTS)
        return np.log10(out + 1)

    def score(self, rows: list[dict], slot: int, bots: set[str] = frozenset()) -> pd.DataFrame:
        """Rows of one block -> frame with features, ``p`` and ``flag``.

        Swaps by known attackers are dropped first, as in training: their front legs would
        otherwise inflate the very pool-flow feature that marks their victims."""
        new = pd.DataFrame(rows)
        if new.empty:
            return new
        new = new[~new["signer"].isin(bots)].sort_values(["tx_index", "ev_index"]).reset_index(drop=True)
        if new.empty:
            return new
        feats = pd.concat([base_features(new), self.ctx.features(new)], axis=1)
        feats["log_pool_hist"] = self._pool_hist(new)
        feats = feats[FEATURES]
        new = pd.concat([new, feats], axis=1)
        new["p"] = self.model.predict(new[FEATURES])
        new["flag"] = new["p"] >= self.threshold
        self.ctx.gc(slot)
        return new
