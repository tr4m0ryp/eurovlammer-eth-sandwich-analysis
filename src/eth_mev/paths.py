"""Filesystem anchors shared by the entry points: the repository root and its ``data/`` directory.

``ETH_MEV_ROOT`` overrides the root; the default assumes the editable ``src/`` layout."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("ETH_MEV_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
