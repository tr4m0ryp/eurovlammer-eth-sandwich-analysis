"""Victim-model scorer: ``p`` = P(this swap is a sandwich target).

The parent trained a LightGBM model on victim rate; here the same *pattern* is kept (load a model if
present, else a calibrated heuristic based on slippage slack and pool flow). The heuristic keeps the
pipeline runnable before a model is trained; the finetune harness retrains against settlement ground
truth. ``p`` is never treated as a confident probability — the allocator clamps it to [0.02, 0.9].
"""
from __future__ import annotations

import json
import math
from pathlib import Path


class Scorer:
    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.threshold = 0.02
        if model_path and model_path.exists():
            try:
                import lightgbm as lgb
                self.model = lgb.Booster(model_file=str(model_path))
                meta = json.loads(model_path.with_suffix(".json").read_text())
                self.threshold = float(meta.get("threshold", 0.02))
            except Exception:
                self.model = None

    def score(self, row: dict) -> float:
        """Return ``p`` for one swap row. Uses the model when present, else a defensible heuristic."""
        if self.model is not None:
            import numpy as np
            feats = self._features(row)
            p = float(self.model.predict(np.array([feats]), num_threads=1)[0])
            return min(max(p, 0.0), 1.0)
        return self._heuristic(row)

    def _features(self, row: dict) -> list[float]:
        # Minimal feature vector (model-dependent; kept tiny so the heuristic and model agree on shape).
        return [
            float(row.get("slack") or 0.0),
            math.log10(float(row.get("x_pre") or 1.0)),
            float(row.get("pool_freq") or 0.0),
        ]

    def _heuristic(self, row: dict) -> float:
        """Calibrated fallback: slack is the strongest single victim signal (the notes' finding).

        Larger slippage slack -> more sandwich room -> higher victim probability; pool crowding adds a
        little. Saturated near 0.4 (the observed max precision at high p) — never pretend we know.
        """
        slack = float(row.get("slack") or 0.0)
        if not math.isfinite(slack) or slack <= 0:
            return 0.05
        freq = min(float(row.get("pool_freq") or 0.0), 5.0)
        p = 0.02 + 0.10 * math.log10(1.0 + slack) + 0.004 * freq
        return min(max(p, 0.0), 0.40)
