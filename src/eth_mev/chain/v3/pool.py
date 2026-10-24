"""Fetch live V3 pool state over JSON-RPC: slot0, liquidity, token0/token1, fee, tickSpacing,
and the initialized tick bitmap (``ticks``/``liquidityNet``) the exposure walk needs."""
from __future__ import annotations

from typing import Any

from .constants import WETH  # noqa: F401


def encode_selector(sig: str) -> str:
    """4-byte function selector for a canonical signature."""
    from web3 import Web3
    return "0x" + Web3.keccak(text=sig).hex()[:8]


_SLOT0 = encode_selector("slot0()")
_LIQUIDITY = encode_selector("liquidity()")
_TOKEN0 = encode_selector("token0()")
_TOKEN1 = encode_selector("token1()")
_FEE = encode_selector("fee()")
_TICKSPACING = encode_selector("tickSpacing()")


def decode_slot0(raw: str) -> dict:
    from eth_abi import decode
    if not raw or raw == "0x":
        return {}
    vals = decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"],
                  bytes.fromhex(raw[2:]))
    return {"sqrt_price_x96": int(vals[0]), "tick": int(vals[1]), "fee_protocol": int(vals[5]),
            "unlocked": bool(vals[6])}


async def fetch_pool_states_bulk(rpc: Any, pools: list[str], block: str = "latest") -> dict[str, dict]:
    """Fetch slot0 + liquidity + token0/token1 + fee + tickSpacing for every pool in ONE batched
    JSON-RPC request (6 selectors × N pools collapse to a single round-trip). Returns pool -> state;
    a failed read leaves defaults so one bad pool never aborts the block."""
    if not pools:
        return {}
    selectors = [("slot0", _SLOT0), ("liquidity", _LIQUIDITY), ("token0", _TOKEN0),
                 ("token1", _TOKEN1), ("fee", _FEE), ("tick_spacing", _TICKSPACING)]
    calls: list[tuple[str, list]] = []
    # order: pool-major (all 6 selectors for pool 0, then pool 1, ...)
    for pool in pools:
        for _name, sel in selectors:
            calls.append(("eth_call", [{"to": pool, "data": sel}, block]))
    results = await rpc.batch_calls(calls)
    from eth_abi import decode as _abi_decode
    out: dict[str, dict] = {}
    n_sel = len(selectors)
    for pi, pool in enumerate(pools):
        chunk = results[pi * n_sel:(pi + 1) * n_sel]
        if any(chunk):  # skip fully-failed pools
            state = _decode_pool_chunk(pool, chunk, _abi_decode)
            out[pool] = state
    return out


def _decode_pool_chunk(pool: str, chunk: list, abi_decode) -> dict:
    state = {"pool": pool, "sqrt_price_x96": 0, "liquidity": 0, "tick": 0,
             "token0": "", "token1": "", "fee": 0, "tick_spacing": 60}
    slot0_raw, liq_raw, t0_raw, t1_raw, fee_raw, ts_raw = (chunk + [None] * 6)[:6]
    try:
        if slot0_raw and slot0_raw != "0x":
            vals = abi_decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"],
                              bytes.fromhex(slot0_raw[2:]))
            state["sqrt_price_x96"], state["tick"] = int(vals[0]), int(vals[1])
    except Exception:
        pass
    for raw, attr, typ in ((liq_raw, "liquidity", "uint128"), (fee_raw, "fee", "uint24"),
                           (ts_raw, "tick_spacing", "int24")):
        try:
            if raw and raw != "0x":
                state[attr] = int(abi_decode([typ], bytes.fromhex(raw[2:]))[0])
        except Exception:
            pass
    for raw, attr in ((t0_raw, "token0"), (t1_raw, "token1")):
        try:
            if raw and raw != "0x":
                state[attr] = abi_decode(["address"], bytes.fromhex(raw[2:]))[0]
        except Exception:
            pass
    if state["fee"] == 0:
        state["fee"] = 3000
    if state["tick_spacing"] == 0:
        state["tick_spacing"] = 60
    state["fee_ppm"] = state["fee"]
    return state


async def fetch_pool_state(rpc: Any, pool: str) -> dict:
    """Single-pool convenience (batches internally to one request)."""
    got = await fetch_pool_states_bulk(rpc, [pool])
    return got.get(pool) or {
        "pool": pool, "sqrt_price_x96": 0, "liquidity": 0, "tick": 0,
        "token0": "", "token1": "", "fee": 3000, "tick_spacing": 60, "fee_ppm": 3000,
    }
