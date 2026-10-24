"""Unit tests: generic embedded-swap scanner and multicall(bytes[]) (undocumented wrapper recovery)."""
from __future__ import annotations

from eth_abi import encode

from eth_mev.chain.v3_swap import decode_embedded_swap, decode_v3_router

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


def _tx(data: bytes, to="0x278d858f053ba533c3adf9ef1a07516b6a07d0a6") -> dict:
    return {"to": to, "input": "0x" + data.hex(), "from": "0x" + "1" * 40, "hash": "0x" + "a" * 64}


def test_embedded_exact_input_single_in_opaque_wrapper():
    # An unknown router wraps a standard exactInputSingle inside arbitrary bytes.
    inner_sel = bytes.fromhex("414bf389")
    inner = encode(["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
                   [DAI, WETH, 3000, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18), 0])
    # wrapper: unknown 4-byte selector + opaque bytes, with the inner call buried at some offset
    wrapper = bytes.fromhex("42e3dcf3") + b"\x00" * 100 + inner_sel + inner
    d = decode_embedded_swap(_tx(wrapper))
    assert d is not None
    assert d["fn"] == "embedded_exactInputSingle"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_embedded_v2_swap_exact_tokens():
    inner_sel = bytes.fromhex("38ed1739")
    inner = encode(["uint256", "uint256", "address[]", "address", "uint256"],
                   [10 ** 18, int(0.95 * 10 ** 18), [DAI, WETH], "0x" + "2" * 40, 1_700_000_000])
    wrapper = bytes.fromhex("771d503f") + b"\x00" * 50 + inner_sel + inner
    d = decode_embedded_swap(_tx(wrapper))
    assert d is not None
    assert d["fn"] == "embedded_v2_exactTokensForTokens"
    assert d["amount_in"] == 10 ** 18
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)


def test_embedded_scan_no_selector_returns_none():
    d = decode_embedded_swap(_tx(bytes.fromhex("deadbeef") + b"\x00" * 64))
    assert d is None


def test_multicall_nodl():
    # multicall(bytes[]) = 0xac9650d8 wrapping exactInputSingle
    inner_sel = bytes.fromhex("414bf389")
    inner = inner_sel + encode(["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"],
                               [DAI, WETH, 3000, "0x" + "2" * 40, 1_700_000_000, 10 ** 18, int(0.95 * 10 ** 18), 0])
    data = bytes.fromhex("ac9650d8") + encode(["bytes[]"], [[inner]])
    d = decode_v3_router(_tx(data, to="0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"))
    assert d is not None
    assert d["fn"] == "exactInputSingle"
    assert d["amount_out_min"] == int(0.95 * 10 ** 18)
