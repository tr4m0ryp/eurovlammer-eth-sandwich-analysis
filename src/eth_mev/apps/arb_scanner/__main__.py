"""run_arb.py — the atomic cross-pool arbitrage screener (realistic dry-run).

Discovers the actual on-chain market universe (Uniswap V3 pools across fee tiers + Uniswap V2 /
SushiSwap pairs for a token list), reads their state in one batched round-trip, detects cross-market
price dislocations, computes the optimal flash-loan arb, and serves the results on the dashboard.

**Dry-run only** — detects + logs + serves, never sends. The live send (executor calldata → gated
``FlashbotsRelay``) is wired but disabled behind ``LIVE=1`` + funded keys.

Usage:
    ETH_RPC_URL=https://eth.drpc.org ETH_WS_URL=wss://eth.drpc.org .venv/bin/python run_arb.py --port 8765
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from eth_mev.chain.constants import (FACTORY_ABI, PAIR_ABI, UNI_V2_FACTORY, SUSHI_V2_FACTORY, WETH)
from eth_mev.chain.v3_constants import FEE_TIERS, UNI_V3_FACTORY
from eth_mev.chain.v3_pool import encode_selector
from eth_mev.ingest.rpc import Rpc, rpc_url, ws_url
from eth_mev.metrics.arb import Market, find_best_arb
from eth_mev.serve.hub import Hub
from eth_mev.serve.server import serve

LOAN_WEI = int(float(os.environ.get("MEV_FLASH_LOAN_ETH", "10")) * 1e18)
FLASH_FEE_BPS = int(os.environ.get("MEV_FLASH_FEE_BPS", "0"))    # Balancer 0%
MIN_PROFIT_WEI = int(float(os.environ.get("MEV_MIN_ARB_PROFIT_ETH", "0.0005")) * 1e18)
MIN_DEPTH_WEI = int(float(os.environ.get("MEV_MIN_DEPTH_ETH", "50")) * 1e18)  # 50 WETH both sides
MAX_PAIR_AGE_S = int(os.environ.get("MEV_MAX_PAIR_AGE_S", "3600"))            # 1h staleness for V2

# The token universe whose WETH-paired markets we watch. WETH is the numeraire hub.
TOKENS = {
    "USDC": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "AAVE": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "PEPE": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
    "SHIB": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    "MKR": "0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2",
}

# V2 factories (Uniswap + SushiSwap) — both use the same Pair ABI.
V2_FACTORIES = [("uniswap_v2", UNI_V2_FACTORY), ("sushiswap_v2", SUSHI_V2_FACTORY)]

_GET_PAIR = encode_selector("getPair(address,address)")
_GET_POOL = encode_selector("getPool(address,address,uint24)")
_GET_RESERVES = encode_selector("getReserves()")
_TOKEN0 = encode_selector("token0()")
_TOKEN1 = encode_selector("token1()")


def _to_addr(v: str) -> str:
    """Decode an address from a 32-byte ABI word (returns '' on a zero / malformed value)."""
    if not v or v == "0x":
        return ""
    b = bytes.fromhex(v[2:].zfill(64))
    a = "0x" + b[-20:].hex()
    return "" if a == "0x" + "0" * 40 else a


def _dec_word(v: str, typ: str):
    from eth_abi import decode
    return decode([typ], bytes.fromhex(v[2:].zfill(64)))[0]


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

    async def _discover_pools(self) -> list[str]:
        """Resolve every token×WETH market address (V3 pools + V2 pairs) in one batched request."""
        calls: list[tuple[str, list]] = []
        # V3: getPool(token, WETH, fee) for each token × fee tier
        for tok in TOKENS.values():
            for fee in FEE_TIERS:
                calls.append(("eth_call", [{"to": UNI_V3_FACTORY, "data": _GET_POOL + _encode_getpool(tok, WETH, fee)}, "latest"]))
        # V2: getPair(token, WETH) for each token × each factory
        for _, fac in V2_FACTORIES:
            for tok in TOKENS.values():
                calls.append(("eth_call", [{"to": fac, "data": _GET_PAIR + _encode_getpair(tok, WETH)}, "latest"]))
        results = await self.rpc.batch_calls(calls)
        pools = []
        for r in results:
            a = _to_addr(r) if isinstance(r, str) else ""
            if a:
                pools.append(a)
        return list(dict.fromkeys(pools))   # dedup, keep order

    async def _read_markets(self, pools: list[str]) -> list[Market]:
        """Read token0/token1 + V3 slot0/liquidity/fee/tickSpacing + V2 reserves for every pool."""
        # First, discover which pools are V2 pairs vs V3 pools by probing getReserves (V2) vs slot0 (V3).
        probe = []
        for p in pools:
            probe.append(("eth_call", [{"to": p, "data": _GET_RESERVES}, "latest"]))
        res = await self.rpc.batch_calls(probe)
        v2_pools, v3_pools = [], []
        for p, r in zip(pools, res):
            if isinstance(r, str) and r != "0x" and len(r) >= 194:   # getReserves returns 3×32 bytes
                v2_pools.append(p)
            else:
                v3_pools.append(p)
        markets = []
        # V3 pools: slot0 + liquidity + token0 + token1 + fee + tickSpacing
        from eth_mev.chain.v3_pool import fetch_pool_states_bulk
        v3_states = await fetch_pool_states_bulk(self.rpc, v3_pools)
        for p, st in v3_states.items():
            if st.get("liquidity") and st.get("sqrt_price_x96"):
                markets.append(Market(
                    token0=st.get("token0", ""), token1=st.get("token1", ""), kind="v3",
                    fee_bps=st.get("fee_ppm", 3000) // 100, address=p,
                    sqrt_price_x96=st["sqrt_price_x96"], liquidity=st["liquidity"],
                    tick=st["tick"], tick_spacing=st["tick_spacing"], ticks={},
                ))
        # V2 pairs: getReserves + token0 + token1
        if v2_pools:
            reads = []
            for p in v2_pools:
                reads.append(("eth_call", [{"to": p, "data": _GET_RESERVES}, "latest"]))
                reads.append(("eth_call", [{"to": p, "data": _TOKEN0}, "latest"]))
                reads.append(("eth_call", [{"to": p, "data": _TOKEN1}, "latest"]))
            rres = await self.rpc.batch_calls(reads)
            for k, p in enumerate(v2_pools):
                base = k * 3
                r0 = rres[base]; t0 = rres[base + 1]; t1 = rres[base + 2]
                if not isinstance(r0, str) or r0 == "0x":
                    continue
                from eth_abi import decode
                r0v, r1v, ts = decode(["uint112", "uint112", "uint32"], bytes.fromhex(r0[2:].zfill(64)))
                markets.append(Market(
                    token0=_to_addr(t0) if isinstance(t0, str) else "",
                    token1=_to_addr(t1) if isinstance(t1, str) else "",
                    kind="v2", fee_bps=30, address=p,
                    reserve0=int(r0v), reserve1=int(r1v), last_updated=int(ts),
                ))
        return markets

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
        pools = await self._discover_pools()
        markets = await self._read_markets(pools)
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
        deadline = time.monotonic() + hours * 3600
        from eth_mev.ingest.rpc import Subscriber
        sub = Subscriber(ws_url() or rpc_url().replace("https://", "wss://"), ["newHeads"])
        try:
            async for msg in sub.messages():
                if time.monotonic() > deadline:
                    break
                if "error" in msg or "params" not in msg:
                    continue
                params = msg.get("params")
                if not isinstance(params, dict):
                    continue
                payload = params.get("result")
                if not (isinstance(payload, dict) and "number" in payload):
                    continue
                try:
                    number = int(payload["number"], 16)
                except (TypeError, ValueError):
                    continue
                try:
                    self.last_block_ts = int(payload.get("timestamp", "0x0"), 16)
                except (TypeError, ValueError):
                    pass
                await self._fees()
                await self.scan_block(number)
        finally:
            await sub.close()


def _encode_getpool(token_a: str, token_b: str, fee: int) -> str:
    from eth_abi import encode
    return encode(["address", "address", "uint24"], [token_a, token_b, fee]).hex()


def _encode_getpair(token_a: str, token_b: str) -> str:
    from eth_abi import encode
    return encode(["address", "address"], [token_a, token_b]).hex()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    async with Rpc(rpc_url(), rps=10, concurrency=8) as rpc:
        screener = ArbScreener(rpc)
        runner = await serve(screener.hub, "0.0.0.0", a.port)
        print(f"arb dry-run on http://0.0.0.0:{a.port}")
        try:
            await screener.run(a.hours)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
