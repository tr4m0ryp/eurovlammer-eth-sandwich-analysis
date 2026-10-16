"""Constant-product legs with fees, in float reserve/base units.

Uniswap V2 prices ``x * y = k`` on reserves and levies one combined fee rate ``r`` (30 bps) on top
of a buy's curve input and out of a sell's curve output. ``x`` is the numeraire (WETH) side, ``y``
the traded token — identical in role to the Solana ``(lamports, token)`` framing, so the math ports
verbatim.
"""
from __future__ import annotations


def tokens_for_net(x: float, y: float, n: float) -> float:
    """Tokens out for ``n`` net numeraire into the curve."""
    return y * n / (x + n)


def net_for_tokens(x: float, y: float, a: float) -> float:
    """Net numeraire needed to take ``a`` tokens out (inf when ``a`` drains the curve)."""
    return x * a / (y - a) if a < y else float("inf")


def sol_for_tokens(x: float, y: float, t: float) -> float:
    """Curve-side numeraire out for ``t`` tokens in (before the sell fee)."""
    return x * t / (y + t)


def after_buy(x: float, y: float, n: float, t: float) -> tuple[float, float]:
    return x + n, y - t


def after_sell(x: float, y: float, o: float, t: float) -> tuple[float, float]:
    return x - o, y + t
