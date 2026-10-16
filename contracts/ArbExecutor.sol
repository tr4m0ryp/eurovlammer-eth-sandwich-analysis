// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.26;

/// @title ArbExecutor: atomic cross-pool arbitrage funded by a flash loan.
/// @notice One transaction: borrow `amount` of `token` (Balancer V2 at 0%, Aave V3 as fallback),
///         run a cycle of V2/V3 swaps that starts and ends in `token`, repay the loan, keep the
///         surplus. Any cycle that does not clear `amount + fee + minProfit` reverts with
///         `NoProfit(out, owed)`, so a dry-run `eth_call` reports the exact on-chain cycle output
///         even for unprofitable candidates.
/// @dev    Owner lives in storage slot 0 (not an immutable) so an `eth_call` state override can
///         install the runtime code plus an owner without a deployment. `pending` and
///         `activePool` use transient storage (EIP-1153, Cancun).
contract ArbExecutor {
    address public owner; // slot 0

    address internal constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address internal constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address internal constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    uint8 internal constant PROVIDER_BALANCER = 0;
    uint8 internal constant PROVIDER_AAVE = 1;
    uint8 internal constant KIND_V2 = 0;
    uint8 internal constant KIND_V3 = 1;

    uint160 internal constant MIN_SQRT_RATIO_P1 = 4295128740;
    uint160 internal constant MAX_SQRT_RATIO_M1 = 1461446703485210103287273052203988822378723970341;

    // transient slots
    bytes32 internal constant PENDING_SLOT = keccak256("arb.pending");
    bytes32 internal constant ACTIVE_POOL_SLOT = keccak256("arb.activePool");

    struct Leg {
        address pool;
        address tokenIn;
        uint8 kind; // KIND_V2 | K