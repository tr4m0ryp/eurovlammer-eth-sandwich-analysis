"""Flash-loan provider integration: Balancer V2 (0% fee) and Aave V3 (0.05%) calldata encoding.

A flash loan is an on-chain smart-contract call (not an API): the borrower contract calls
``flashLoan``/``flashLoanSimple``, receives the tokens, does its swaps inside the callback, and
repays before the tx ends. This module encodes those calls; the borrower contract is ``ArbExecutor``
(see ``contracts/ArbExecutor.sol``), and the Python side only signs + submits one tx to it.

Selectors verified via ``web3.keccak`` (never trusted from memory).
"""
from __future__ import annotations

from eth_abi import encode

# Balancer V2 Vault — 0% flash-loan fee (the preferred lender).
BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
BALANCER_FLASH_LOAN_SEL = "0x5c38449e"   # flashLoan(address,address[],uint256[],bytes)
BALANCER_RECEIVE_SEL = "0xf04f2707"      # receiveFlashLoan(address[],uint256[],uint256[],bytes)

# Aave V3 Pool — ~0.05% flash-loan fee (fallback).
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
AAVE_FLASH_LOAN_SIMPLE_SEL = "0x42b0b77c"  # flashLoanSimple(address,address,uint256,bytes,uint16)
AAVE_EXECUTE_OP_SEL = "0x1b11d0ff"        # executeOperation(address,uint256,uint256,address,bytes)

BALANCER_FLASH_LOAN_ABI = [
    {"inputs": [
        {"internalType": "address", "name": "recipient", "type": "address"},
        {"internalType": "address[]", "name": "tokens", "type": "address[]"},
        {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"},
        {"internalType": "bytes", "name": "userData", "type": "bytes"},
    ], "name": "flashLoan", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

AAVE_FLASH_LOAN_SIMPLE_ABI = [
    {"inputs": [
        {"internalType": "address", "name": "receiverAddress", "type": "address"},
        {"internalType": "address", "name": "asset", "type": "address"},
        {"internalType": "uint256", "name": "amount", "type": "uint256"},
        {"internalType": "bytes", "name": "params", "type": "bytes"},
        {"internalType": "uint16", "name": "referralCode", "type": "uint16"},
    ], "name": "flashLoanSimple", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]


def balancer_flash_loan_calldata(recipient: str, tokens: list[str], amounts: list[int],
                                 user_data: bytes) -> str:
    """Encode ``Vault.flashLoan(recipient, tokens, amounts, userData)``."""
    args = encode(["address", "address[]", "uint256[]", "bytes"],
                  [recipient, tokens, amounts, user_data])
    return BALANCER_FLASH_LOAN_SEL + args.hex()


def aave_flash_loan_simple_calldata(receiver: str, asset: str, amount: int, params: bytes,
                                    referral_code: int = 0) -> str:
    """Encode ``Pool.flashLoanSimple(receiver, asset, amount, params, referralCode)``."""
    args = encode(["address", "address", "uint256", "bytes", "uint16"],
                  [receiver, asset, amount, params, referral_code])
    return AAVE_FLASH_LOAN_SIMPLE_SEL + args.hex()
