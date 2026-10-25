"""eth_mev: measurement of sandwich-attack exposure on Ethereum DEXs (research).

Ingest the public mempool and confirmed blocks, decode Uniswap V2/V3 and router swaps, measure each
swap's sandwich exposure, label real sandwiches by inclusion order and replay the opportunities
through a counterfactual allocation model. Everything runs dry; ``exec/`` is gated and disabled.
"""
from __future__ import annotations

__version__ = "0.1.0"