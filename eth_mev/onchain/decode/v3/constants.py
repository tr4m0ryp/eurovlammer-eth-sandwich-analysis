"""Ethereum mainnet Uniswap V3 constants: addresses, ABI fragments, event topics."""
from __future__ import annotations

# Canonical Uniswap V3 mainnet deployments.
UNI_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNI_V3_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"       # SwapRouter02 (most common)
UNI_V3_ROUTER_OLD = "0xE592427A0AEce92De3Edee1F18E0157C05861564"  # SwapRouter (V1, struct-only)
UNI_V3_QUOTER = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"      # Quoter V2
UNI_V3_POSITION_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

V3_ROUTERS = {
    UNI_V3_ROUTER: "uniswap_v3",
    UNI_V3_ROUTER_OLD: "uniswap_v3",
}

# Correct function selectors (verified via keccak of the canonical signatures).
# SwapRouter02 wraps most swaps in ``multicall(uint256,bytes[])`` → nested exactInputSingle/exactInput.
V3_SELECTORS = {
    "0x414bf389": "exactInputSingle",   # exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))
    "0xdb3e2198": "exactOutputSingle",  # exactOutputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))
    "0xc04b8d59": "exactInput",         # exactInput((bytes,address,uint256,uint256,uint256))
    "0xf28c0498": "exactOutput",        # exactOutput((bytes,address,uint256,uint256,uint256))
    "0x5ae401dc": "multicall",          # multicall(uint256,bytes[])
    "0xac9650d8": "multicall_nodl",     # multicall(bytes[])  (no deadline)
}

# ExactInputParams / ExactOutputParams struct field layouts (ABI tuple order).
#   exactInput:  (bytes path, address recipient, uint256 deadline, uint256 amountIn, uint256 amountOutMinimum)
#   exactOutput: (bytes path, address recipient, uint256 deadline, uint256 amountOut, uint256 amountInMaximum)
# Single structs: (tokenIn, tokenOut, fee, recipient, deadline, amount, amountLimit, sqrtPriceLimitX96)
EXACT_INPUT_TYPES = ["bytes", "address", "uint256", "uint256", "uint256"]
EXACT_OUTPUT_TYPES = ["bytes", "address", "uint256", "uint256", "uint256"]
SINGLE_TYPES = ["address", "address", "uint24", "address", "uint256", "uint256", "uint256", "uint160"]

# Swap event topic (keccak of the V3 Swap signature).
V3_SWAP_EVENT_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Pool.slot0() / liquidity() / token0() / token1() / fee() / tickSpacing()
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

FEE_TIERS = (100, 500, 3000, 10000)   # 0.01%, 0.05%, 0.3%, 1%
