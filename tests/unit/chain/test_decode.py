"""Unit tests: Uniswap V2 swap decoding (calldata + Swap logs, defensive decode)."""
from __future__ import annotations

import eth_abi

from eth_mev.chain.v2.constants import ROUTERS, SWAP_EVENT_TOPIC
from eth_mev.chain.v2.swaps import decode_swap_logs, decode_v2_router


ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
TOK = "0x6B175474E89094C44Da98b954EedeAC495271d0F"  # DAI


def _swap_calldata(amount_in: int, amount_out_min: int, path: list[str], deadline: int) -> str:
    sel = bytes.fromhex("38ed1739")  # swapExactTokensForTokens
    args = eth_abi.encode(["uint256", "uint256", "address[]", "address", "uint256"],
                          [amount_in, amount_out_min, path, "0x" + "0" * 40, deadline])
    return "0x" + (sel + args).hex()


def test_decode_v2_router_exact_in():
    path = [WETH, TOK]
    data = _swap_calldata(1000, 900, path, 1234567890)
    tx = {"to": ROUTER, "input": data, "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}
    out = decode_v2_router(tx, ROUTERS)
    assert out is not None and len(out) == 1
    d = out[0]
    assert d["is_exact_in"] is True
    assert d["amount"] == 1000
    assert d["limit"] == 900
    assert d["deadline"] == 1234567890
    assert d["token_a"].lower() == WETH.lower() and d["token_b"].lower() == TOK.lower()


def test_decode_v2_router_ignores_unknown_to():
    tx = {"to": "0x" + "9" * 40, "input": "0x1234", "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}
    assert decode_v2_router(tx, ROUTERS) is None


def test_decode_v2_router_case_insensitive_to():
    # Real providers return lower-cased ``to`` addresses; the decoder must match them.
    path = [WETH, TOK]
    data = _swap_calldata(1000, 900, path, 1234567890)
    tx = {"to": ROUTER.lower(), "input": data, "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}
    out = decode_v2_router(tx, ROUTERS)
    assert out is not None and out[0]["amount"] == 1000 and out[0]["limit"] == 900


def test_decode_v2_router_malformed_returns_none():
    tx = {"to": ROUTER, "input": "0x", "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}
    assert decode_v2_router(tx, ROUTERS) is None


def _swap_log(tx_hash, log_index, a0i, a1i, a0o, a1o, pool="0x" + "b" * 40):
    data = eth_abi.encode(["uint256", "uint256", "uint256", "uint256"], [a0i, a1i, a0o, a1o]).hex()
    return {"address": pool, "topics": [SWAP_EVENT_TOPIC], "data": "0x" + data,
            "transactionHash": tx_hash, "logIndex": hex(log_index)}


def test_decode_swap_logs_buy():
    # buy: token1 (y) out == 500, token0 (numeraire) in == 100
    logs = [_swap_log("0x" + "a" * 64, 0, 100, 0, 0, 500)]
    swaps = decode_swap_logs("0x" + "a" * 64, 1, 0, logs, WETH,
                             token_by_pool={logs[0]["address"]: (WETH, TOK)},
                             venue_by_pool={logs[0]["address"]: "uniswap_v2"})
    assert len(swaps) == 1
    s = swaps[0]
    assert s.is_buy is True
    assert s.amount_in == 100
    assert s.amount_out == 500


def test_decode_swap_logs_sell():
    # sell: token1 (y) in == 500, token0 out == 90
    logs = [_swap_log("0x" + "a" * 64, 0, 0, 500, 90, 0)]
    swaps = decode_swap_logs("0x" + "a" * 64, 1, 0, logs, WETH,
                             token_by_pool={logs[0]["address"]: (WETH, TOK)},
                             venue_by_pool={logs[0]["address"]: "uniswap_v2"})
    assert len(swaps) == 1
    s = swaps[0]
    assert s.is_buy is False
    assert s.amount_in == 500
    assert s.amount_out == 90


def test_decode_swap_logs_skips_unknown_topic():
    logs = [{"address": "0x" + "b" * 40, "topics": ["0x" + "0" * 64], "data": "0x" + "0" * 64}]
    swaps = decode_swap_logs("0x" + "a" * 64, 1, 0, logs, WETH)
    assert swaps == []
