"""Constant-product legs with fees, in float lamports/base units.

Both venues price ``x * y = k`` on effective reserves and levy one combined fee rate ``r`` on top
of a buy's curve input and out of a sell's curve output. ``x`` is the SOL side, ``y`` the token.
"""
from __future__ import annotations


def tokens_for_net(x: float, y: float, n: float) -> float:
    """Tokens out for ``n`` net lamports into the curve."""
    return y * n / (x + n)


def net_for_tokens(x: float, y: float, a: float) -> float:
    """Net lamports needed to take ``a`` tokens out (inf when ``a`` drains the curve)."""
    return x * a / (y - a) if a < y else float("inf")


def sol_for_tokens(x: float, y: float, t: float) -> float:
    """Curve-side lamports out for ``t`` tokens in (before the sell fee)."""
    return x * t / (y + t)


def after_buy(x: float, y: float, n: float, t: float) -> tuple[float, float]:
    return x + n, y - t


def after_sell(x: float, y: float, o: float, t: float) -> tuple[float, float]:
    return x - o, y + t
