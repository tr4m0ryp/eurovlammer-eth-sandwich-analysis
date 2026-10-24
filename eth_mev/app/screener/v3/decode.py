"""Victim-guard decoding chain for one confirmed transaction.

Tries each decoder in turn: direct Uniswap router, UniversalRouter ``execute``, aggregator,
embedded-standard-swap scan, then the private MEV-bot routers. The first clean decode wins.
"""
from __future__ import annotations

from typing import Any

from ....onchain.decode.routers.aggregators import decode_aggregator
from ....onchain.decode.routers.private import decode_private_router
from ....onchain.decode.v3.swaps import decode_embedded_swap, decode_universal_router, decode_v3_router


def decode_victim(n: dict[str, Any], raw_input: str | None) -> tuple[dict | None, bool]:
    """Return ``(calldata, via_aggregator)`` for normalised tx ``n`` with raw calldata ``raw_input``."""
    tx = {"to": n["to"], "input": raw_input, "hash": n["hash"], "from": n["from"]}
    calldata = decode_v3_router(tx)
    if calldata is None:
        calldata = decode_universal_router(tx)
    if calldata is None:
        calldata = decode_aggregator(n["to"], raw_input, tx)
        if calldata is not None:
            return calldata, True
    if calldata is None:
        calldata = decode_embedded_swap(tx)
    if calldata is None:
        calldata = decode_private_router(tx)
    return calldata, False
