"""Swap calldata builder: encode the two legs of a V2 sandwich (front-run + back-run).

Pure encoding (no key, no signing): ``swapExactTokensForTokens`` / ``swapExactETHForTokens`` /
``swapExactTokensForETH`` calldata for a single-hop path through the router. The bundle assembly
(live) is separate in ``flashbots.py``.
"""
from __future__ import annotations

from eth_abi import encode

_SELECTORS = {
    "swapExactTokensForTokens": b"\x38\xed\x17\x39",
    "swapExactETHForTokens": b"\x7f\xf3\x6a\xb5",
    "swapExactTokensForETH": b"\x18\xcb\xaf\xe5",
}


def swap_calldata(fn: str, amount_in: int, amount_out_min: int, path: list[str],
                  to: str, deadline: int) -> str:
    """Return the hex calldata for a router swap."""
    sel = _SELECTORS[fn]
    args = encode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        [amount_in, amount_out_min, [a for a in path], to, deadline],
    )
    return "0x" + (sel + args).hex()


def checksum(path: list[str]) -> list[str]:
    """Checksum a token path (defensive: addresses may arrive lower-cased)."""
    from eth_utils import to_checksum_address
    return [to_checksum_address(a) for a in path]


def front_run_calldata(router: str, token_in: str, token_out: str, amount_in: int,
                       recipient: str, deadline: int, is_eth_in: bool = False) -> str:
    """Front leg: buy the token (or sell it) to move the price ahead of the victim."""
    path = [token_in, token_out]
    fn = "swapExactETHForTokens" if is_eth_in and token_in == _WETH_LOWER else "swapExactTokensForTokens"
    return swap_calldata(fn, amount_in, 1, path, recipient, deadline)


def back_run_calldata(router: str, token_in: str, token_out: str, amount_in: int,
                      recipient: str, deadline: int, to_eth: bool = False) -> str:
    """Back leg: unwind the front position, walking the price back."""
    path = [token_in, token_out]
    fn = "swapExactTokensForETH" if to_eth else "swapExactTokensForTokens"
    return swap_calldata(fn, amount_in, 1, path, recipient, deadline)


_WETH_LOWER = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower()
