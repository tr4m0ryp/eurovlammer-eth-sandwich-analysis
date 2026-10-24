"""Unit tests: Uniswap UniversalRouter ``execute`` command decoding."""
from __future__ import annotations

from eth_abi import encode

from eth_mev.chain.v3.swaps import decode_universal_router

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _tx(data: bytes) -> dict:
    return {"to": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            "input": "0x" + data.hex(), "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}


def _path_encoded() -> bytes:
    # DAI -> (3000) -> WETH
    return bytes.fromhex(DAI[2:]) + (3000).to_bytes(3, "big") + bytes.fromhex(WETH[2:])


def test_decode_execute_v3_swap_exact_in():
    # commands: [0x00] (V3_SWAP_EXACT_IN)
    commands = bytes([0x00])
    swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"],
                        ["0x" + "2" * 40, 10 ** 18, int(0.95 * 10 ** 18), _path_encoded(), False])
    data = bytes.fromhex("24856bc3") + encode(["bytes", "bytes[]"], [commands, [swap_input]])
    d = decode_universal_router(_tx(data))
    assert d is not None
    assert d["fn"] == "universal_router_execute"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)
    assert d["token_in"].lower() == DAI.lower()
    assert d["token_out"].lower() == WETH.lower()
    assert d["fee"] == 3000


def test_decode_execute_with_deadline():
    # execute(bytes,bytes[],uint256) = 0x3593564c
    commands = bytes([0x00])
    swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"],
                        ["0x" + "2" * 40, 10 ** 18, int(0.95 * 10 ** 18), _path_encoded(), False])
    data = bytes.fromhex("3593564c") + encode(["bytes", "bytes[]", "uint256"],
                                              [commands, [swap_input], 1_700_000_000])
    d = decode_universal_router(_tx(data))
    assert d is not None
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_decode_execute_v3_swap_exact_out():
    # V3_SWAP_EXACT_OUT = 0x01: (recipient, amountOut, amountInMax, path, payerIsUser)
    commands = bytes([0x01])
    swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"],
                        ["0x" + "2" * 40, 10 ** 18, int(1.05 * 10 ** 18), _path_encoded(), False])
    data = bytes.fromhex("24856bc3") + encode(["bytes", "bytes[]"], [commands, [swap_input]])
    d = decode_universal_router(_tx(data))
    assert d is not None
    # exact out: victim wants `amount` out (guard = amount), pays <= amount_in_max
    assert d["amount_out_min"] == 10 ** 18
    assert d["amount_in"] == int(1.05 * 10 ** 18)


def test_decode_execute_skips_non_swap_commands():
    # commands: [0x0b (WRAP_ETH), 0x00 (V3_SWAP_EXACT_IN)] — must find the swap
    commands = bytes([0x0b, 0x00])
    wrap_input = encode(["address", "uint256"], ["0x" + "2" * 40, 10 ** 18])
    swap_input = encode(["address", "uint256", "uint256", "bytes", "bool"],
                        ["0x" + "2" * 40, 10 ** 18, int(0.95 * 10 ** 18), _path_encoded(), False])
    data = bytes.fromhex("24856bc3") + encode(["bytes", "bytes[]"], [commands, [wrap_input, swap_input]])
    d = decode_universal_router(_tx(data))
    assert d is not None and d["fn"] == "universal_router_execute"
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_decode_execute_no_swap_command_returns_none():
    commands = bytes([0x0b])  # only WRAP_ETH
    wrap_input = encode(["address", "uint256"], ["0x" + "2" * 40, 10 ** 18])
    data = bytes.fromhex("24856bc3") + encode(["bytes", "bytes[]"], [commands, [wrap_input]])
    assert decode_universal_router(_tx(data)) is None


def test_decode_execute_unknown_selector_returns_none():
    data = bytes.fromhex("deadbeef") + b"\x00" * 64
    assert decode_universal_router(_tx(data)) is None
