"""Unit tests: aggregator slippage-guard decoders (1inch, 0x/Matcha, Paraswap)."""
from __future__ import annotations

from eth_abi import encode

from eth_mev.chain.routers.aggregators import decode_aggregator

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _tx(data: bytes, to="0x1111111254eeb25477b68fb85ed929f73a960582") -> dict:
    return {"to": to, "input": "0x" + data.hex(), "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}


def test_oneinch_v5_swap_min_return():
    desc = encode(["address", "address", "address", "address", "uint256", "uint256", "uint256"],
                  [DAI, WETH, "0x" + "2" * 40, "0x" + "3" * 40, 10 ** 18, int(0.97 * 10 ** 18), 0])
    data = bytes.fromhex("07ed2379") + encode(["address", "(address,address,address,address,uint256,uint256,uint256)", "bytes"],
                                              ["0x" + "4" * 40, [DAI, WETH, "0x" + "2" * 40, "0x" + "3" * 40, 10 ** 18, int(0.97 * 10 ** 18), 0], b""])
    d = decode_aggregator(_tx(data)["to"], "0x" + data.hex(), _tx(data))
    assert d is not None
    assert d["fn"] == "oneinch_swap"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.97 * 10 ** 18)
    assert d["token_in"].lower() == DAI.lower()
    assert d["token_out"].lower() == WETH.lower()


def test_oneinch_unoswap_min_return():
    args = encode(["address", "uint256", "uint256", "uint256[]"], [DAI, 10 ** 18, int(0.97 * 10 ** 18), []])
    data = bytes.fromhex("17203bbd") + args
    d = decode_aggregator("0x1111111254eeb25477b68fb85ed929f73a960582", "0x" + data.hex(), _tx(data))
    assert d is not None and d["fn"] == "oneinch_unoswap"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.97 * 10 ** 18)


def test_zeroex_transform_min_out():
    args = encode(["address", "address", "uint256", "uint256", "(uint32,bytes)[]"],
                  [DAI, WETH, 10 ** 18, int(0.97 * 10 ** 18), []])
    data = bytes.fromhex("415565b0") + args
    d = decode_aggregator("0xdef1c0ded9bec7f1a1670819833240f0276b5eff", "0x" + data.hex(), _tx(data))
    assert d is not None and d["fn"] == "zeroex_transformERC20"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.97 * 10 ** 18)


def test_zeroex_sell_to_uniswap_min_buy():
    args = encode(["address[]", "uint256", "uint256", "bool"], [[DAI, WETH, USDC], 10 ** 18, int(0.97 * 10 ** 18), False])
    data = bytes.fromhex("d9627aa4") + args
    d = decode_aggregator("0xdef1c0ded9bec7f1a1670819833240f0276b5eff", "0x" + data.hex(), _tx(data))
    assert d is not None and d["fn"] == "zeroex_sellToUniswap"
    assert d["amount_out_min"] == int(0.97 * 10 ** 18)
    assert d["token_in"].lower() == DAI.lower()
    assert d["token_out"].lower() == USDC.lower()  # last token in path


def test_unknown_selector_returns_none():
    data = bytes.fromhex("deadbeef") + b"\x00" * 32
    assert decode_aggregator("0x1111111254eeb25477b68fb85ed929f73a960582", "0x" + data.hex(), _tx(data)) is None


def test_non_aggregator_returns_none():
    assert decode_aggregator("0x" + "9" * 40, "0x07ed2379", _tx(b"")) is None
