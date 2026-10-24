"""Unit tests: Uniswap V3 router calldata decoding (single-hop, multi-hop, multicall)."""
from __future__ import annotations

from eth_abi import encode

from eth_mev.chain.v3_swap import decode_v3_router, _decode_path
from eth_mev.chain.v3_constants import UNI_V3_ROUTER, WETH

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDC = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _tx(data: bytes) -> dict:
    return {"to": UNI_V3_ROUTER, "input": "0x" + data.hex(), "from": "0x" + "1" * 40,
            "hash": "0x" + "a" * 64}


def test_decode_exact_input_single():
    sel = bytes.fromhex("414bf389")
    args = encode(["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
                  [DAI, WETH, 3000, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18), 0])
    d = decode_v3_router(_tx(sel + args))
    assert d is not None
    assert d["fn"] == "exactInputSingle"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)
    assert d["fee"] == 3000
    assert d["token_in"].lower() == DAI.lower()
    assert d["token_out"].lower() == WETH.lower()


def test_decode_exact_input_multihop_path():
    sel = bytes.fromhex("c04b8d59")
    # path: DAI -> (3000) -> WETH -> (500) -> USDC  (2 hops)
    path = bytes.fromhex(DAI[2:]) + (3000).to_bytes(3, "big") + bytes.fromhex(WETH[2:]) + \
           (500).to_bytes(3, "big") + bytes.fromhex(USDC[2:])
    args = encode(["bytes", "address", "uint256", "uint256", "uint256"],
                  [path, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18)])
    d = decode_v3_router(_tx(sel + args))
    assert d is not None
    assert d["fn"] == "exactInput"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)
    assert d["n_hops"] == 2
    assert d["token_in"].lower() == DAI.lower()
    assert d["token_out"].lower() == WETH.lower()   # first hop


def test_decode_multicall_nested_exact_input_single():
    # multicall(uint256 deadline, bytes[] data) wrapping exactInputSingle
    inner_sel = bytes.fromhex("414bf389")
    inner_args = encode(["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
                        [DAI, WETH, 3000, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18), 0])
    inner = inner_sel + inner_args
    mc_sel = bytes.fromhex("5ae401dc")
    mc_args = encode(["uint256", "bytes[]"], [1_700_000_000, [inner]])
    d = decode_v3_router(_tx(mc_sel + mc_args))
    assert d is not None
    assert d["fn"] == "exactInputSingle"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_decode_path():
    path = bytes.fromhex(DAI[2:]) + (3000).to_bytes(3, "big") + bytes.fromhex(WETH[2:])
    hops = _decode_path(path)
    assert hops == [(DAI.lower(), WETH.lower(), 3000)]


def test_decode_unknown_selector_returns_none():
    d = decode_v3_router(_tx(bytes.fromhex("deadbeef") + b"\x00" * 32))
    assert d is None


def test_decode_non_router_returns_none():
    # Unknown selector -> None (BUT a *known swap selector* decodes regardless of 'to', tested below)
    d = decode_v3_router({"to": "0x" + "9" * 40, "input": "0xdeadbeef", "from": "0x" + "1" * 40,
                          "hash": "0x" + "a" * 64})
    assert d is None


def test_decode_by_selector_regardless_of_to():
    # MEV-bot routers reuse exactInputSingle with a non-Uniswap 'to'; must still decode.
    sel = bytes.fromhex("414bf389")
    args = encode(["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
                  [DAI, WETH, 3000, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18), 0])
    d = decode_v3_router({"to": "0xdfa8ead9127f91b5025ee1e8576e9b946467c7a5",
                          "input": "0x" + (sel + args).hex(), "from": "0x" + "1" * 40,
                          "hash": "0x" + "a" * 64})
    assert d is not None
    assert d["fn"] == "exactInputSingle"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_direction_sign_convention():
    """amount0 > 0 = token0 INTO pool = token0→token1 (zeroForOne). Verified against a traced
    pool swap where the trader paid WBTC (amount0=+11.14e6) and received WETH (amount1=-3.7e18)."""
    from eth_mev.chain.v3_swap import V3Swap
    s = V3Swap(tx_hash="0x", block_number=1, tx_index=0, log_index=0, pool="0x", token0="WBTC",
               token1="WETH", fee=3000, user="", amount0=11_143_553, amount1=-3_707_451_493_023_300_189,
               sqrt_price_x96=0, liquidity=0, tick=0)
    assert s.direction == 1          # token0 (WBTC) paid in → token0→token1
    assert s.is_buy is True          # pays token0, receives token1

    # reverse: trader pays token1, receives token0 (amount0 < 0)
    s2 = V3Swap(tx_hash="0x", block_number=1, tx_index=0, log_index=0, pool="0x", token0="WBTC",
                token1="WETH", fee=3000, user="", amount0=-11_143_553, amount1=3_707_451_493_023_300_189,
                sqrt_price_x96=0, liquidity=0, tick=0)
    assert s2.direction == -1         # token1→token0
    assert s2.is_buy is False
