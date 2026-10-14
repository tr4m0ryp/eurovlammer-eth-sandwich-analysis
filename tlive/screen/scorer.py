"""Score each new swap with the trained model, using the exact offline feature code.

Context features need earlier swaps, so the scorer keeps the last ``HIST_SLOTS`` of swaps and
recomputes context over history + new block, keeping only the new rows. That costs ~10 ms a block
and buys bit-identical parity with the backtest.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from ..metrics.features import FEATURES, features

HIST_SLOTS = 160


class Scorer:
    def __init__(self, model_path: Path):
        self.model = lgb.Booster(model_file=str(model_path))
        meta = json.loads(model_path.with_suffix(".json").read_text())
        self.threshold = float(meta["threshold"])
        self.meta = meta
        self.hist = pd.DataFrame()

    def score(self, rows: list[dict], slot: int) -> pd.DataFrame:
        """Rows of one block -> frame with features, ``p`` and ``flag``."""
        new = pd.DataFrame(rows)
        if new.empty:
            return new
        base = pd.concat([self.hist, new], ignore_index=True) if len(self.hist) else new.copy()
        feats = features(base).iloc[len(base) - len(new):].reset_index(drop=True)
        new = pd.concat([new.reset_index(drop=True), feats], axis=1)
        new["p"] = self.model.predict(new[FEATURES])
        new["flag"] = new["p"] >= self.threshold
        keep = base[base["slot"] > slot - HIST_SLOTS]
        self.hist = keep[[c for c in keep.columns if c not in FEATURES]].reset_index(drop=True)
        return new
