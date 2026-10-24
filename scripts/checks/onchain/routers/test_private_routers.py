"""Unit tests: reverse-engineered private-router decoders (0x51c72848, 0x278d858f)."""
from __future__ import annotations

from eth_abi import encode

from eth_mev.onchain.decode.routers.private import decode_51c7, decode_278d, ROUTER_51C7, ROUTER_278D

POOL = "0xe0554a476a092703abdb3ef35c80e0d76d32939f"
USDC = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _tx(data: bytes) -> dict:
    return {"to": ROUTER_51C7, "input": "0x" + data.hex(), "from": "0x" + "1" * 40,
            "hash": "0x" + "a" * 64}


def test_51c7_swap_no_deadline():
    # 0x771d503f(address, address pool, uint256 amountIn, uint256 minAmountOut, bool zeroForOne, bytes)
    data = bytes.fromhex("771d503f") + encode(
        ["address", "address", "uint256", "uint256", "bool", "bytes"],
        ["0x" + "0" * 40, POOL, 11_143_553, 3_707_451_493_023_300_189, True,
         bytes.fromhex(USDC[2:])])
    d = decode_51c7(_tx(data))
    assert d is not None
    assert d["fn"] == "51c7_swap"
    assert d["pool"].lower() == POOL.lower()
    assert d["amount_in"] == 11_143_553
    assert d["amount_out_min"] == 3_707_451_493_023_300_189
    assert d["zero_for_one"] is True


def test_51c7_swap_with_deadline():
    # 0x122067ed(..., uint256 deadline, bytes)
    data = bytes.fromhex("122067ed") + encode(
        ["address", "address", "uint256", "uint256", "bool", "uint256", "bytes"],
        ["0x" + "0" * 40, POOL, 8_063_071_745, 3_269_879_415_177_609_216, True,
         1_791_067_310, bytes.fromhex(USDC[2:])])
    d = decode_51c7(_tx(data))
    assert d is not None
    assert d["amount_in"] == 8_063_071_745
    assert d["amount_out_min"] == 3_269_879_415_177_609_216
    assert d["deadline"] == 1_791_067_310


def test_51c7_wrong_selector_returns_none():
    data = bytes.fromhex("deadbeef") + b"\x00" * 64
    assert decode_51c7(_tx(data)) is None


def test_51c7_wrong_address_returns_none():
    data = bytes.fromhex("771d503f") + encode(
        ["address", "address", "uint256", "uint256", "bool", "bytes"],
        ["0x" + "0" * 40, POOL, 1, 2, True, b""])
    assert decode_51c7({"to": "0x" + "9" * 40, "input": "0x" + data.hex(),
                        "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}) is None


def _tx278(data: bytes) -> dict:
    return {"to": ROUTER_278D, "input": "0x" + data.hex(), "from": "0x" + "1" * 40,
            "hash": "0x" + "a" * 64}


def test_278d_swap():
    # 0x42e3dcf3(uint256 amountIn, address token, uint256 amountOutMin, bool zeroForOne,
    #             address pool, uint256 deadline, uint256 flag)
    token = "0x9d65ff81a3c488d585bbfb0bfe3c7707c7917f54"
    data = bytes.fromhex("42e3dcf3") + encode(
        ["uint256", "address", "uint256", "bool", "address", "uint256", "uint256"],
        [40_113_585_581_102_374_456, token, 43_065_939_229_066_838, True,
         "0x0a55882f4e1117adc54afbbac4a39079ad52fb89", 1_791_229_795, 18])
    d = decode_278d(_tx278(data))
    assert d is not None
    assert d["fn"] == "278d_swap"
    assert d["amount_in"] == 40_113_585_581_102_374_456
    assert d["amount_out_min"] == 43_065_939_229_066_838
    assert d["zero_for_one"] is True
    assert d["token_out"].lower() == token.lower()


def test_278d_wrong_selector_returns_none():
    data = bytes.fromhex("deadbeef") + b"\x00" * 224
    assert decode_278d(_tx278(data)) is None

