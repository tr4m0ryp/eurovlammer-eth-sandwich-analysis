"""Unit tests: atomic arb math (V2 + V3 unified), flash-loan calldata, executor calldata."""
from __future__ import annotations

from eth_mev.measure.arb import Market, find_best_arb, two_leg_profit
from eth_mev.measure.exposure.v3.tick_math import price_to_sqrt_x96
from eth_mev.strategy.execution.arb.flash_loan import (aave_flash_loan_simple_calldata, balancer_flash_loan_calldata)
from eth_mev.strategy.execution.arb.build import _leg, execute_arb_calldata

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _v3(price: float, fee_bps: int = 30, L: int = 10 ** 30) -> Market:
    return Market(token0=WETH, token1=USDC, kind="v3", fee_bps=fee_bps,
                  sqrt_price_x96=price_to_sqrt_x96(price), liquidity=L, tick=0, tick_spacing=60)


def _v2(price: float, fee_bps: int = 30, reserve0: int = 10 ** 24) -> Market:
    return Market(token0=WETH, token1=USDC, kind="v2", fee_bps=fee_bps,
                  reserve0=reserve0, reserve1=int(reserve0 * price))


def test_v3_two_leg_profit_positive_dislocation():
    a = _v3(1.02)   # token1 cheap here
    b = _v3(1.00)
    tok, out, fee = two_leg_profit(a, b, 10 * 10 ** 18, 5)
    profit = out - 10 * 10 ** 18 - fee
    assert profit > 0
    assert 0.10 * 10 ** 18 < profit < 0.20 * 10 ** 18


def test_v2_two_leg_profit_positive_dislocation():
    a = _v2(1.02)
    b = _v2(1.00)
    tok, out, fee = two_leg_profit(a, b, 10 * 10 ** 18, 5)
    profit = out - 10 * 10 ** 18 - fee
    assert profit > 0
    assert 0.10 * 10 ** 18 < profit < 0.20 * 10 ** 18


def test_cross_venue_v2_v3_arb():
    a = _v3(1.02, fee_bps=5)   # V3 0.05% pool, token1 cheap
    b = _v2(1.00, fee_bps=30)  # V2 pair, token1 expensive
    tok, out, fee = two_leg_profit(a, b, 10 * 10 ** 18, 5)
    profit = out - 10 * 10 ** 18 - fee
    assert profit > 0


def test_reverse_dislocation_loses():
    a = _v3(1.00)
    b = _v3(1.02)
    tok, out, fee = two_leg_profit(a, b, 10 * 10 ** 18, 5)
    assert out - 10 * 10 ** 18 - fee < 0


def test_find_best_arb():
    markets = [_v3(1.00), _v3(1.02), _v2(1.015)]
    r = find_best_arb(markets, 10 * 10 ** 18, 5, 0)
    assert r is not None
    assert r.profit_wei > 0
    assert r.buy is not None and r.sell is not None
    assert r.buy.price > r.sell.price


def test_flash_fee_adds_to_cost():
    tok, out, fee = two_leg_profit(_v3(1.02), _v3(1.00), 10 * 10 ** 18, 5)
    assert fee == int(10 * 10 ** 18 * 5 / 10000) == int(0.005 * 10 ** 18)


def test_balancer_calldata_selector():
    calldata = balancer_flash_loan_calldata("0x" + "1" * 40, ["0x" + "2" * 40], [10 ** 18], b"")
    assert calldata.startswith("0x5c38449e")


def test_aave_calldata_selector():
    calldata = aave_flash_loan_simple_calldata("0x" + "1" * 40, "0x" + "2" * 40, 10 ** 18, b"")
    assert calldata.startswith("0x42b0b77c")


def test_executor_calldata_roundtrip():
    buy = _leg("0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40, 10 ** 18, 4295128740, True)
    sell = _leg("0x" + "d" * 40, "0x" + "c" * 40, "0x" + "b" * 40, 9 * 10 ** 18, 1461446703485210103287273052203988822378723970341, False)
    cd = execute_arb_calldata("0x" + "b" * 40, 10 ** 18, "0x" + "c" * 40, 10 ** 15, buy, sell, "balancer")
    from web3 import Web3
    sel = Web3.keccak(text="executeArbBalancer(address,uint256,bytes)")[:4].hex()
    assert cd.startswith("0x" + sel)
    assert len(cd) > 8


def test_executor_calldata_aave_provider():
    buy = _leg("0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40, 10 ** 18, 4295128740, True)
    sell = _leg("0x" + "d" * 40, "0x" + "c" * 40, "0x" + "b" * 40, 9 * 10 ** 18, 1461446703485210103287273052203988822378723970341, False)
    cd = execute_arb_calldata("0x" + "b" * 40, 10 ** 18, "0x" + "c" * 40, 10 ** 15, buy, sell, "aave")
    from web3 import Web3
    sel = Web3.keccak(text="executeArbAave(address,uint256,bytes)")[:4].hex()
    assert cd.startswith("0x" + sel)
