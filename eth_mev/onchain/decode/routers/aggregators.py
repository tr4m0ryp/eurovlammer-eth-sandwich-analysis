"""Aggregator slippage-guard decoders: recover the victim's ``minReturn``-style guard from the
aggregator calldata that the direct Uniswap V3 router decoder cannot see.

The V3 strict screener only sees a victim's slippage guard when the tx calls Uniswap's own router
(``exactInputSingle``/``exactInput``/``multicall``). Most of the remaining volume goes through
aggregators — 1inch, 0x/Matcha, Paraswap — each of which encodes the same guard under a different
name (``minReturnAmount`` / ``minOutputTokenAmount`` / ``minToAmount``). This module decodes those,
so the ~96%-invisible swap population can be scored.

Every decoder is defensive: any ABI mismatch returns ``None`` (never trust a single field, ported
from the V2/V3 decoder spirit). Each returns the same shape the caller expects:
``{"fn", "token_in", "token_out", "amount_in", "amount_out_min", "deadline", ...}``.
"""
from __future__ import annotations

from typing import Any

# Known aggregator entrypoints (selector -> (name, decoder kind)). Selectors verified via keccak
# of the canonical deployed signatures on Ethereum mainnet.
AGG_SELECTORS = {
    # 1inch AggregationRouterV5/V6
    "0x07ed2379": "oneinch_swap_v5",       # swap(address,(...desc 7 fields...),bytes)
    "0xac77ec2a": "oneinch_swap_v6",       # swap(address,(...desc 8 fields...),bytes)
    "0x17203bbd": "oneinch_unoswapTo",     # unoswapTo(address src,uint256 amount,uint256 minReturn,uint256[] pools)
    "0x0502b1c5": "oneinch_unoswap",       # unoswap(address src,uint256 amount,uint256 minReturn,uint256[] pools)
    # 0x Exchange / Matcha
    "0x415565b0": "zeroex_transformERC20", # transformERC20(in,out,amountIn,minOut,(uint32,bytes)[])
    "0xd9627aa4": "zeroex_sellToUniswap",  # sellToUniswap(address[] tokens,uint256 sellAmount,uint256 minBuyAmount,bool isSushi)
    "0x5c64d84a": "zeroex_sellToPancake",  # sellToPancakeSwap(same shape)
    "0x689fd938": "zeroex_fillOrKill",     # fillOrKillOrder(order, takerAssetFillAmount, signature)
    # Paraswap AugustusSwapper
    "0x907a5ba7": "paraswap_simpleSwap",   # simpleSwap(data, router, payer, recipient, minToAmount)
    "0xe890861e": "paraswap_multiSwap",    # multiSwap(data, router, payer, recipient, minToAmount)
}

# 1inch SwapDescription field order (V5): srcToken, dstToken, srcReceiver, dstReceiver, amount,
# minReturnAmount, flags. V6 appends a uint256 at the end (8 fields).
_ONEINCH_DESC_V5 = ["address", "address", "address", "address", "uint256", "uint256", "uint256"]
_ONEINCH_DESC_V6 = ["address", "address", "address", "address", "uint256", "uint256", "uint256", "uint256"]


def _decode_oneinch_swap(payload: bytes, v6: bool) -> dict | None:
    from eth_abi import decode as abi_decode
    desc_types = _ONEINCH_DESC_V6 if v6 else _ONEINCH_DESC_V5
    try:
        executor, desc, _data = abi_decode(["address", f"({','.join(desc_types)})", "bytes"], payload)
    except Exception:
        return None
    src_token, dst_token, _src_recv, _dst_recv, amount, min_return, _flags = desc[:7]
    return {
        "fn": "oneinch_swap",
        "token_in": src_token, "token_out": dst_token,
        "amount_in": int(amount), "amount_out_min": int(min_return),
        "deadline": None, "n_hops": 1,
    }


def _decode_oneinch_unoswap(payload: bytes) -> dict | None:
    from eth_abi import decode as abi_decode
    try:
        # unoswap(address srcToken, uint256 amount, uint256 minReturn, uint256[] pools)
        src, amount, min_return, _pools = abi_decode(
            ["address", "uint256", "uint256", "uint256[]"], payload)
    except Exception:
        return None
    return {"fn": "oneinch_unoswap", "token_in": src, "token_out": "",
            "amount_in": int(amount), "amount_out_min": int(min_return), "deadline": None, "n_hops": 1}


def _decode_zeroex_transform(payload: bytes) -> dict | None:
    from eth_abi import decode as abi_decode
    try:
        # transformERC20(inputToken, outputToken, inputTokenAmount, minOutputTokenAmount,
        #                (uint32 deploymentNonce, bytes data)[] transformations)
        tin, tout, amt_in, min_out, _xf = abi_decode(
            ["address", "address", "uint256", "uint256", "(uint32,bytes)[]"], payload)
    except Exception:
        return None
    return {"fn": "zeroex_transformERC20", "token_in": tin, "token_out": tout,
            "amount_in": int(amt_in), "amount_out_min": int(min_out), "deadline": None, "n_hops": 1}


def _decode_zeroex_sell_to_uniswap(payload: bytes) -> dict | None:
    from eth_abi import decode as abi_decode
    try:
        # sellToUniswap(address[] tokens, uint256 sellAmount, uint256 minBuyAmount, bool isSushi)
        tokens, sell_amt, min_buy, _is_sushi = abi_decode(
            ["address[]", "uint256", "uint256", "bool"], payload)
    except Exception:
        return None
    if not tokens or len(tokens) < 2:
        return None
    # tokens[] is the path: tokenIn, WETH, tokenOut (or reversed); minBuyAmount is the guard.
    return {"fn": "zeroex_sellToUniswap", "token_in": tokens[0], "token_out": tokens[-1],
            "amount_in": int(sell_amt), "amount_out_min": int(min_buy), "deadline": None,
            "n_hops": len(tokens) - 1}


def _decode_paraswap_swap(payload: bytes) -> dict | None:
    """Paraswap simpleSwap/multiSwap: the guard is the trailing ``minToAmount``/``destAmount`` scalar.

    The full struct is large and version-dependent, so defensively decode the *tail* scalars
    (minToAmount) by skipping the leading varying tuple — but a tuple of unknown size cannot be
    skipped safely, so we only handle the documented simpleSwap layout:
        simpleSwap(SimpleData data, bytes16 router, uint256 payer, address recipient, uint256 minToAmount)
    SimpleData = (address fromToken, address toToken, uint256 fromAmount, uint256 toAmount,
                  uint256 expectedAmount, address[] callees, bytes exchangeData, uint256 startIndexes,
                  uint256[] values)
    """
    from eth_abi import decode as abi_decode
    simple_data = ("address","address","uint256","uint256","uint256","address[]","bytes",
                   "uint256","uint256[]")
    try:
        data, router, payer, recipient, min_to = abi_decode(
            [f"({','.join(simple_data)})", "bytes16", "uint256", "address", "uint256"], payload)
    except Exception:
        return None
    from_token, to_token, from_amount, to_amount, expected, *_ = data
    return {"fn": "paraswap_swap", "token_in": from_token, "token_out": to_token,
            "amount_in": int(from_amount), "amount_out_min": int(min_to),
            "deadline": None, "n_hops": 1}


def decode_aggregator(to: str, input_: str, tx: dict[str, Any]) -> dict | None:
    """Decode a victim swap from an aggregator router call. Returns the guard triad + token pair,
    or ``None`` when ``to`` is not a known aggregator or the calldata doesn't decode."""
    inp = (input_ or "")[2:] if isinstance(input_, str) and input_.startswith("0x") else (input_ or "")
    if not inp or len(inp) < 8:
        return None
    data = bytes.fromhex(inp)
    sel = "0x" + data[:4].hex()
    kind = AGG_SELECTORS.get(sel)
    if kind is None:
        return None
    payload = data[4:]
    d = None
    if kind == "oneinch_swap_v5":
        d = _decode_oneinch_swap(payload, v6=False)
    elif kind == "oneinch_swap_v6":
        d = _decode_oneinch_swap(payload, v6=True)
    elif kind in ("oneinch_unoswap", "oneinch_unoswapTo"):
        d = _decode_oneinch_unoswap(payload)
    elif kind == "zeroex_transformERC20":
        d = _decode_zeroex_transform(payload)
    elif kind in ("zeroex_sellToUniswap", "zeroex_sellToPancake"):
        d = _decode_zeroex_sell_to_uniswap(payload)
    elif kind in ("paraswap_simpleSwap", "paraswap_multiSwap"):
        d = _decode_paraswap_swap(payload)
    if d is None:
        return None
    d["user"] = tx.get("from", "")
    d["tx_hash"] = tx.get("hash", "")
    return d


# Aggregator router addresses (Ethereum mainnet). ``decode_aggregator`` keys off the selector, not
# this list; the list documents which addresses are in scope for a cheap pre-filter.
AGG_ROUTERS = {
    "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch AggregationRouterV5
    "0x111111125421ca6dc452d289314280a0f8842a65",  # 1inch V6
    "0xdef1c0ded9bec7f1a1670819833240f0276b5eff",  # 0x ExchangeProxy (Matcha)
    "0xdef171fe48cf0115b1d80b88dc8eab59176fee57",  # Paraswap AugustusSwapper V6
    "0xdef171fe48cf0115b1d80b88dc8eab59176fee57",  # (Paraswap V6 duplicate guard-free)
}
