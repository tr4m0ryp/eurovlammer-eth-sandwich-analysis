"""Uniswap V2 (and SushiSwap-style forks): addresses, ABIs, pools and swap decoding."""
from __future__ import annotations

from .swaps import EXACT_IN, EXACT_OUT, Swap, decode_swap_logs, decode_v2_router  # noqa: F401
