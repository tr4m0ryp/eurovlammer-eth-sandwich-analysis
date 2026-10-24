"""The atomic cross-pool arbitrage scanner (dry-run): detect WETH-numeraire price dislocations per block.

Reads the market universe each block, keeps tradeable markets (depth + staleness filter), computes
the optimal flash-loan arbitrage and publishes it to the dashboard. Measurement only: nothing is sent.
"""
from __future__ import annotations

import os
import time

from ....onchain.decode.v2.constants import WETH
from ....onchain.node import Rpc, new_heads
from ....measure.arb import Market, find_best_arb
from ...dashboard.hub import Hub
from .markets import discover_pools, read_markets

LOAN_WEI = int(float(os.environ.get("MEV_FLASH_LOAN_ETH", "10")) * 1e18)
FLASH_FEE_BPS = int(os.environ.get("MEV_FLASH_FEE_BPS", "0"))    # Balancer 0%
MIN_PROFIT_WEI = int(float(os.environ.get("MEV_MIN_ARB_PROFIT_ETH", "0.0005")) * 1e18)
MIN_DEPTH_WEI = int(float(os.environ.get("MEV_MIN_DEPTH_ETH", "50")) * 1e18)  # 50 WETH both sides
MAX_PAIR_AGE_S = int(os.environ.get("MEV_MAX_PAIR_AGE_S", "3600"))            # 1h staleness for V2


class ArbScreener:
    def __init__(self, rpc: Rpc):
        self.rpc = rpc
        self.hub = Hub()
        self.n_blocks = 0
        self.n_arbs = 0
        self.n_markets = 0
        self.last_base_fee = 20_000_000_000      # 20 gwei fallback
        self.gas_bribe_wei = 0
        self.last_block_ts = 0                   # current block timestamp (for staleness filter)
        self.n_filtered = 0                      # markets rejected by the tradeability filter

    async def _fees(self) -> None:
        try:
            hist = await self.rpc.fee_history(4, "latest", [50])
            self.last_base_fee = int(hist["baseFeePerGas"][-1], 16) if hist else 20_000_000_000
        except Exception:
            self.last_base_fee = 20_000_000_000
        # executor tx: ~5 legs (borrow + 2 swaps + approvals + repay) ≈ 500k gas; bribe small for arb
        self.gas_bribe_wei = int(500_000 * self.last_base_fee) + int(0.0001 * 1e18)

    def _tradeable(self, m: Market) -> bool:
        """Tradeability filter: enough WETH depth, and (for V2) a fresh last-touched timestamp.

        Excludes the thin and the stale (the 44%-profit stale-pair artifact): a market must hold at
        least ``MIN_DEPTH_WEI`` WETH and a V2 pair must have been touched within ``MAX_PAIR_AGE_S``."""
        if m.depth_weth < MIN_DEPTH_WEI:
            return False
        if m.kind == "v2" and m.last_updated is not None and self.last_block_ts:
            if self.last_block_ts - m.last_updated > MAX_PAIR_AGE_S:
                return False
        return True

    async def scan_block(self, number: int) -> None:
        pools = await discover_pools(self.rpc)
        markets = await read_markets(self.rpc, pools)
        self.n_markets = max(self.n_markets, len(markets))
        # orient every WETH-paired market WETH-as-token0 (the borrowed numeraire), then tradeability-filter
        oriented = [m.orient(WETH) for m in markets
                    if WETH.lower() in (m.token0.lower(), m.token1.lower()) and m.price > 0]
        filtered = [m for m in oriented if self._tradeable(m)]
        self.n_filtered = len(oriented) - len(filtered)
        r = find_best_arb(filtered, LOAN_WEI, FLASH_FEE_BPS, MIN_PROFIT_WEI)
        self.n_blocks += 1
        if r and r.profit_wei > self.gas_bribe_wei:
            self.n_arbs += 1
            self.hub.add_strict([{
                "pool_buy": r.buy.address[:14], "pool_sell": r.sell.address[:14],
                "price_buy": r.buy.price, "price_sell": r.sell.price,
                "kind_buy": r.buy.kind, "kind_sell": r.sell.kind,
                "spread_pct": (r.buy.price / r.sell.price - 1) * 100 if r.sell.price else 0,
                "borrow": r.borrow_wei, "profit": r.profit_wei,
                "net": r.profit_wei - self.gas_bribe_wei,
                "depth_buy": r.buy.depth_weth, "depth_sell": r.sell.depth_weth,
                "token1": r.buy.token1[:12], "seen_ms": int(time.time() * 1000),
            }])
        self.hub.set_stats({
            "blocks": self.n_blocks, "arbs": self.n_arbs, "block": self.n_blocks,
            "markets": self.n_markets, "filtered": self.n_filtered,
            "loan_eth": LOAN_WEI / 1e18, "flash_fee_bps": FLASH_FEE_BPS,
            "base_fee_gwei": self.last_base_fee / 1e9, "min_depth_eth": MIN_DEPTH_WEI / 1e18,
        })

    async def run(self, hours: float) -> None:
        async for head in new_heads(hours):
            try:
                self.last_block_ts = int(head.get("timestamp", "0x0"), 16)
            except (TypeError, ValueError):
                pass
            await self._fees()
            await self.scan_block(head["number"])
