"""Filesystem anchors shared by the entry points.

``REPO`` is this checkout (docs, figures and reports are written here). ``ROOT`` is the data root,
overridable with ``ETH_MEV_ROOT`` so run databases can live elsewhere without moving repo outputs."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("ETH_MEV_ROOT", REPO))
DATA_DIR = ROOT / "data"
