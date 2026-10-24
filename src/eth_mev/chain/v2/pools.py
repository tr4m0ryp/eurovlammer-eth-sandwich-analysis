"""Pool resolution helpers: token0/token1 and reserves for a V2 pair address.

Pure computation over already-fetched RPC results (the async fetching lives in ``ingest/rpc.py``).
``reserve1/reserve0`` is the pool's numeraire price when token0 is the numeraire (WETH); here we
always orient ``x`` = numeraire (WETH) side, ``y`` = traded token side.
"""
from __future__ import annotations


def orient(token0: str, token1: str, numeraire: str) -> tuple[bool, str, str]:
    """Return (token0_is_numeraire, x_token, y_token): which side is the numeraire (WETH/stable)."""
    if token0.lower() == numeraire.lower():
        return True, token0, token1
    if token1.lower() == numeraire.lower():
        return False, token1, token0
    # Neither side is the numeraire: assume token0 is the reference (a stable-paired pool).
    return True, token0, token1


def reserves_to_xy(token0_is_numeraire: bool, reserve0: int, reserve1: int) -> tuple[int, int]:
    """Return (x, y) reserve units orienting x = numeraire side, y = traded-token side."""
    return (reserve0, reserve1) if token0_is_numeraire else (reserve1, reserve0)


def pair_key(token0: str, token1: str) -> str:
    """Canonical (sorted) key so (A,B) and (B,A) map to the same pair."""
    return "|".join(sorted([token0.lower(), token1.lower()]))
