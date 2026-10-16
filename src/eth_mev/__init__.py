"""eth_mev — Ethereum mempool-sandwich engine (Uniswap V2 first).

Build-to-production: ingest the public mempool, decode V2 swaps, measure their sandwich exposure,
gate + allocate capital, and settle against inclusion-order ground truth (mev-inspect style).
The live execution path lives in ``exec/`` but is disabled until the operator provides funded keys
and explicitly flips the ``LIVE`` gate.
"""
from __future__ import annotations

__version__ = "0.1.0"