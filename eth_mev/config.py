"""Filesystem anchors shared by the entry points: the repository root and its ``data/`` directory.

``ETH_MEV_ROOT`` overrides the root; the default assumes the package sitting at the repo root."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("ETH_MEV_ROOT", Path(__file__).resolve().parents[1]))
DATA_DIR = ROOT / "data"
