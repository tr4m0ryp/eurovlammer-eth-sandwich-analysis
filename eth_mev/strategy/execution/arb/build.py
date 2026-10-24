"""Encode the ``ArbExecutor`` calldata the bot signs and submits (one tx per arb).

The executor (``contract/ArbExecutor.sol``) does the flash-loan + two swap legs on-chain. This
module encodes ``executeArbBalancer``/``executeArbAave`` with an ``abi.encode(tokenOut, minProfit,
buyLeg, sellLeg)`` payload — the exact shape the contract's ``_runCycle`` decodes.
"""
from __future__ import annotations

from eth_abi import encode


# The Leg struct layout must match ArbExecutor.sol exactly.
LEG_TYPES = ["address", "address", "address", "uint256", "uint160", "bool"]


def _selector(sig: str) -> str:
    from web3 import Web3
    return "0x" + Web3.keccak(text=sig)[:4].hex()


def _leg(pool: str, token_in: str, token_out: str, amount: int, sqrt_limit: int,
         zero_for_one: bool) -> tuple:
    return (pool, token_in, token_out, amount, sqrt_limit, zero_for_one)


def _arb_params(token_out: str, min_profit: int, buy_leg: tuple, sell_leg: tuple) -> bytes:
    # matches Solidity: abi.encode(address tokenOut, uint256 minProfit, Leg buyLeg, Leg sellLeg)
    return encode(["address", "uint256", f"({','.join(LEG_TYPES)})", f"({','.join(LEG_TYPES)})"],
                  [token_out, min_profit, buy_leg, sell_leg])


def execute_arb_calldata(token: str, amount: int, token_out: str, min_profit: int,
                         buy_leg: tuple, sell_leg: tuple, provider: str = "balancer") -> str:
    """Encode the executor entrypoint. ``buy_leg``/``sell_leg`` are 6-tuples from ``_leg``."""
    params = _arb_params(token_out, min_profit, buy_leg, sell_leg)
    if provider == "aave":
        sel = _selector("executeArbAave(address,uint256,bytes)")
        return sel + encode(["address", "uint256", "bytes"], [token, amount, params]).hex()
    sel = _selector("executeArbBalancer(address,uint256,bytes)")
    return sel + encode(["address", "uint256", "bytes"], [token, amount, params]).hex()
