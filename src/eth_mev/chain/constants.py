"""Ethereum mainnet Uniswap V2 / SushiSwap constants: addresses, ABIs, tokens."""
from __future__ import annotations

# Ethereum mainnet addresses (checksummed). These are canonical deployed contracts.
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
UNI_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
UNI_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
SUSHI_V2_FACTORY = "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac"
SUSHI_V2_ROUTER = "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"

# Known V2 forks we also decode (factory addresses); their router ABI is identical.
FACTORIES = {
    "uniswap_v2": UNI_V2_FACTORY,
    "sushiswap_v2": SUSHI_V2_FACTORY,
}
# Router addresses we recognise when a tx targets a fork (used for calldata decoding).
ROUTERS = {
    UNI_V2_ROUTER: "uniswap_v2",
    SUSHI_V2_ROUTER: "sushiswap_v2",
}

# --- ABI fragments (the only selectors we touch) ---

# Pair.getReserves() -> (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Factory.getPair(tokenA, tokenB) -> pair address
FACTORY_ABI = [
    {
        "constant": True,
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Uniswap V2 Router02 swap entrypoints (the victim-indicating calldata we decode).
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETH",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint256", "name": "amountInMax", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapTokensForExactTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# Pair.Swap event: (sender, uint amount0In, uint amount1In, uint amount0Out, uint amount1Out, to)
SWAP_EVENT_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
)

# Router swap function selectors (first 4 bytes of the call data), used to pick the decoder
# without needing web3's ABI registry up front.
SELECTOR_TO_KIND = {
    "0x38ed1739": ("swapExactTokensForTokens", "exact_in"),
    "0x7ff36ab5": ("swapExactETHForTokens", "exact_in_eth"),
    "0x18cbafe5": ("swapExactTokensForETH", "exact_in"),
    "0x8803dbee": ("swapTokensForExactTokens", "exact_out"),
    "0xfb3bdb41": ("swapETHForExactTokens", "exact_out_eth"),
}
