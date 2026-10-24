"""Market universe for the arb scanner: token list, factory lookups and batched pool-state reads.

Discovers every token x WETH market (Uniswap V3 pools across fee tiers, Uniswap V2 and SushiSwap
pairs) and reads their state in batched JSON-RPC round-trips.
"""
from __future__ import annotations

from eth_abi import decode, encode

from ....onchain.decode.v2.constants import SUSHI_V2_FACTORY, UNI_V2_FACTORY, WETH
from ....onchain.decode.v3.constants import FEE_TIERS, UNI_V3_FACTORY
from ....onchain.decode.v3.pool import encode_selector, fetch_pool_states_bulk
from ....onchain.node import Rpc
from ....measure.arb import Market

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


async def discover_pools(rpc: Rpc) -> list[str]:
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
    results = await rpc.batch_calls(calls)
    pools = []
    for r in results:
        a = _to_addr(r) if isinstance(r, str) else ""
        if a:
            pools.append(a)
    return list(dict.fromkeys(pools))   # dedup, keep order

async def read_markets(rpc: Rpc, pools: list[str]) -> list[Market]:
    """Read token0/token1 + V3 slot0/liquidity/fee/tickSpacing + V2 reserves for every pool."""
    # First, discover which pools are V2 pairs vs V3 pools by probing getReserves (V2) vs slot0 (V3).
    probe = []
    for p in pools:
        probe.append(("eth_call", [{"to": p, "data": _GET_RESERVES}, "latest"]))
    res = await rpc.batch_calls(probe)
    v2_pools, v3_pools = [], []
    for p, r in zip(pools, res):
        if isinstance(r, str) and r != "0x" and len(r) >= 194:   # getReserves returns 3×32 bytes
            v2_pools.append(p)
        else:
            v3_pools.append(p)
    markets = []
    # V3 pools: slot0 + liquidity + token0 + token1 + fee + tickSpacing
    v3_states = await fetch_pool_states_bulk(rpc, v3_pools)
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
        rres = await rpc.batch_calls(reads)
        for k, p in enumerate(v2_pools):
            base = k * 3
            r0 = rres[base]; t0 = rres[base + 1]; t1 = rres[base + 2]
            if not isinstance(r0, str) or r0 == "0x":
                continue
            r0v, r1v, ts = decode(["uint112", "uint112", "uint32"], bytes.fromhex(r0[2:].zfill(64)))
            markets.append(Market(
                token0=_to_addr(t0) if isinstance(t0, str) else "",
                token1=_to_addr(t1) if isinstance(t1, str) else "",
                kind="v2", fee_bps=30, address=p,
                reserve0=int(r0v), reserve1=int(r1v), last_updated=int(ts),
            ))
    return markets


def _encode_getpool(token_a: str, token_b: str, fee: int) -> str:
    return encode(["address", "address", "uint24"], [token_a, token_b, fee]).hex()


def _encode_getpair(token_a: str, token_b: str) -> str:
    return encode(["address", "address"], [token_a, token_b]).hex()
