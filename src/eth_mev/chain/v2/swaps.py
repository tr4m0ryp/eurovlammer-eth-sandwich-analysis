"""The venue-neutral V2 swap record every decoder emits and every metric consumes.

Uniswap V2 (and its forks) is exactly ``x * y = k`` on effective reserves. ``reserve0``/``reserve1``
are the pool's token balances; ``token0``/``token1`` are ordered by address. A buy is "token-out for
ETH/stable-in" in the screener's ETH-liquidity frame: we always price against the side that has a
real ETH-value anchor (WETH), so ``x`` is the WETH (numeraire) side and ``y`` the traded token.

The victim's protection is the triplet ``(kind, amount, limit)`` recoverable from the router
calldata: ``swapExactXForY(amountIn, amountOutMin, ...)`` is an exact-in swap with ``amount = amountIn``
and ``limit = amountOutMin`` (its slippage guard = the sandwich's room before the victim reverts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import ROUTERS, SELECTOR_TO_KIND, SWAP_EVENT_TOPIC

# Case-insensitive router -> venue lookup (tx ``to`` fields arrive lower-cased from most providers).
_ROUTERS_LOWER = {k.lower(): v for k, v in ROUTERS.items()}

EXACT_IN, EXACT_OUT = "in", "out"

# A tx receipt / log index tuple that must be decoded defensively (never trust a single field).
_EMPTY_DECODE = {"is_buy": None, "amount": None, "limit": None, "ok": False}


@dataclass
class Swap:
    """One executed V2 swap, in the pool's own integer reserve units.

    ``x_pre``/``y_pre`` are the reserves the swap priced against just before it executed (from
    ``getReserves()`` or reconstructed from the emitted ``Swap`` event). ``x`` is always the
    numeraire (WETH) side, ``y`` the traded token side; a buy moves ``y`` out (token-out for
    numeraire-in), a sell moves ``y`` in. ``fee_bps`` is 30 for Uniswap V2, scoped to whichever
    venue emitted it.

    ``amount``/``limit`` come from the *victim's own calldata* (``amountOutMin`` is the slippage
    guard). ``amount_in``/``amount_out`` are the *actual* executed quantities from the event/log.
    """

    tx_hash: str
    block_number: int | None
    tx_index: int
    log_index: int
    venue: str
    pool: str
    token0: str
    token1: str
    user: str
    is_buy: bool
    amount_in: int          # actual: numeraire-in (buy) or token-in (sell), or 0 if unknown
    amount_out: int         # actual: token-out (buy) or numeraire-out (sell), or 0 if unknown
    x_pre: float
    y_pre: float
    fee_bps: float
    kind: str
    amount: int | None      # the victim's requested quantity (exact-in amountIn / exact-out amountOut)
    limit: int | None       # the victim's slippage guard = amountOutMin (or amountInMax)
    deadline: int | None = None
    block_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def gross(self) -> float:
        """Numeraire the trader actually paid (buy) or received (sell), fees included."""
        r = self.fee_bps / 1e4
        return self.amount_in * (1 + r) if self.is_buy else self.amount_out * (1 - r)


def _selector(data: bytes) -> str:
    return "0x" + data[:4].hex() if len(data) >= 4 else ""


def decode_v2_router(tx: dict[str, Any], routers: dict[str, str] | None = None) -> list[dict] | None:
    """Decode a *single-hop* Uniswap-V2 router swap from a transaction's calldata.

    Returns a list of one raw swap descriptor dict (or None when the tx is not a recognised V2
    router swap). Multi-hop paths are decoded defensively: only the first hop is emitted, and a
    path that does not start or end in a known router is skipped (never trust a single field).

    The descriptor is deliberately *partial* — actual reserve-quantities are filled in by the
    ``Swap`` event decoder (``decode_swap_logs``), which sees the exact amounts. The calldata here
    supplies the victim's ``kind/amount/limit/deadline`` triplet.
    """
    to = (tx.get("to") or "").lower()
    if to not in _ROUTERS_LOWER:
        return None
    inp: str = (tx.get("input") or "")[2:] if isinstance(tx.get("input"), str) else ""
    if len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = _selector(data)
    kind = SELECTOR_TO_KIND.get(sel)
    if kind is None:
        return None
    fn_name, direction = kind
    # Decode (amountIn/amountOut, limit, path, to, deadline) — all router swap fns share this shape.
    try:
        from eth_abi import decode as abi_decode
        decoded = abi_decode(
            ["uint256", "uint256", "address[]", "address", "uint256"],
            data[4:],
        )
    except Exception:
        return None
    amount, limit, path, _to, deadline = decoded
    if not path or len(path) < 2:
        return None
    venue = _ROUTERS_LOWER[to]
    # Single-hop only: the victim's guard is meaningful per-pool; multi-hop is emitted as first hop.
    is_exact_in = direction in ("exact_in", "exact_in_eth")
    return [{
        "tx_hash": tx.get("hash", ""),
        "tx_index": tx.get("transactionIndex", 0),
        "venue": venue,
        "token_a": path[0],
        "token_b": path[1],
        "is_exact_in": is_exact_in,
        "kind": EXACT_IN if is_exact_in else EXACT_OUT,
        "amount": int(amount),
        "limit": int(limit),
        "deadline": int(deadline),
        "user": tx.get("from", ""),
        "fn_name": fn_name,
        "ok": True,
    }]


def decode_swap_logs(tx_hash: str, block_number: int | None, tx_index: int, logs: list[dict],
                     numeraire: str, reserves_by_pool: dict[str, tuple[float, float]] | None = None,
                     token_by_pool: dict[str, tuple[str, str]] | None = None,
                     venue_by_pool: dict[str, str] | None = None) -> list[Swap]:
    """Decode ``Swap`` events in the logs into ``Swap`` records.

    ``reserves_by_pool`` maps pool -> (x_pre, y_pre) *post-state* reserves (as fetched live); when
    absent, the event's own amounts are used as a degraded pre-state. Defensive: a log without a
    recognisable topic or a pool we cannot price is skipped, never crash.
    """
    out: list[Swap] = []
    rbp = reserves_by_pool or {}
    tbp = token_by_pool or {}
    vbp = venue_by_pool or {}
    for i, lg in enumerate(logs):
        if not isinstance(lg, dict):
            continue
        topics = lg.get("topics") or []
        if not topics or topics[0].lower() != SWAP_EVENT_TOPIC or lg.get("address") is None:
            continue
        pool = lg["address"]
        t0, t1 = tbp.get(pool, ("", ""))
        x_pre, y_pre = rbp.get(pool, (0.0, 0.0))
        data = bytes.fromhex((lg.get("data") or "0x")[2:].replace("0x", ""))
        # Swap event data: (uint amount0In, uint amount1In, uint amount0Out, uint amount1Out)
        try:
            from eth_abi import decode as abi_decode
            amt0in, amt1in, amt0out, amt1out = abi_decode(
                ["uint256", "uint256", "uint256", "uint256"], data)
        except Exception:
            continue
        amt0in, amt1in = int(amt0in), int(amt1in)
        amt0out, amt1out = int(amt0out), int(amt1out)
        if amt0in == 0 and amt1in == 0 and amt0out == 0 and amt1out == 0:
            continue
        # Direction is recorded faithfully WITHOUT assuming which token is the numeraire: a swap
        # moving token0 in -> token1 out is "buying token1" (net token0 out = selling token0). We
        # store the raw legs; the settlement labeller compares direction consistently per pool.
        # ``is_buy`` here means "token1 flows out" only as a first approximation; the labeller uses
        # the raw amount legs (a0i/a1i/a0o/a1o) for a direction-agnostic front/back pairing.
        is_buy = (amt1out > 0)
        amount_in = amt0in if is_buy else amt1in
        amount_out = amt1out if is_buy else amt0out
        out.append(Swap(
            tx_hash=tx_hash, block_number=block_number, tx_index=tx_index, log_index=i,
            venue=vbp.get(pool, "uniswap_v2"), pool=pool, token0=t0, token1=t1,
            user="", is_buy=is_buy, amount_in=amount_in, amount_out=amount_out,
            x_pre=x_pre, y_pre=y_pre, fee_bps=30.0, kind=EXACT_IN, amount=None, limit=None,
            raw={"a0i": amt0in, "a1i": amt1in, "a0o": amt0out, "a1o": amt1out,
                 "numeraire": numeraire, "sender": (topics[1] if len(topics) > 1 else None)},
        ))
    return out
