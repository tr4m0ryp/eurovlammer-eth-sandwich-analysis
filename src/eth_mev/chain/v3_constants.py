"""Ethereum mainnet Uniswap V3 constants: addresses, ABI fragments, event topics."""
from __future__ import annotations

# Canonical Uniswap V3 mainnet deployments.
UNI_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNI_V3_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"       # SwapRouter02 (most common)
UNI_V3_ROUTER_OLD = "0xE592427A0AEce92De3Edee1F18E0157C05861564"  # SwapRouter
UNI_V3_QUOTER = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"      # Quoter V2
UNI_V3_POSITION_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

V3_ROUTERS = {
    UNI_V3_ROUTER: "uniswap_v3",
    UNI_V3_ROUTER_OLD: "uniswap_v3",
}

# Router selectors we recognise (exact-in single-hop via SwapRouter02).
# swapExactInputSingle((address tokenIn, address tokenOut, uint24 fee, address recipient,
#   uint256 deadline, uint256 amountIn, uint256 amountOutMinimum, uint160 sqrtPriceLimitX96))
V3_SELECTORS = {
    "0x04e45aaf": "exactInputSingle",   # exactInputSingle (tuple)
    "0x414bf389": "exactInputSingle",   # exactInputSingle on SwapRouter (exactInputSingle(params))
    "0xc04b8d59": "exactInputSingle",   # alt selector
    "0xdb3e2198": "exactOutputSingle",  # exactOutputSingle
}

# Swap event topic (keccak of the V3 Swap signature).
V3_SWAP_EVENT_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Pool.slot0() -> (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, ...)
# Pool.liquidity() -> uint128
# Pool.token0()/token1() -> address
# Pool.fee() -> uint24
# Pool.tickSpacing() -> int24
POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [
        {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
        {"internalType": "int24", "name": "tick", "type": "int24"},
        {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
        {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
        {"internalType": "bool", "name": "unlocked", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "liquidity", "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "fee", "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "tickSpacing", "outputs": [{"internalType": "int24", "name": "", "type": "int24"}], "stateMutability": "view", "type": "function"},
]

# Factory.getPool(tokenA, tokenB, fee) -> pool address
FACTORY_ABI = [
    {"inputs": [
        {"internalType": "address", "name": "tokenA", "type": "address"},
        {"internalType": "address", "name": "tokenB", "type": "address"},
        {"internalType": "uint24", "name": "fee", "type": "uint24"},
    ], "name": "getPool", "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
     "stateMutability": "view", "type": "function"},
]

# Common fee tiers.
FEE_TIERS = (100, 500, 3000, 10000)   # 0.01%, 0.05%, 0.3%, 1%
