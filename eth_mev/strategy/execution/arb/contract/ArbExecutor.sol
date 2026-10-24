// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.19;

/// @title ArbExecutor — atomic cross-pool arbitrage funded by a flash loan.
/// @notice One transaction: borrow `amount` of `token` (Balancer V2 0% / Aave V3 0.05%), run a
///         buy→sell cycle that starts and ends in `token`, repay the loan, keep the surplus.
///         The two swap legs are Uniswap V3 `swap(...)`; adapt the target ABI if the detector finds
///         a V2 pair or a router instead. Any cycle that does not clear `amount + fee + minProfit`
///         reverts with `NoProfit(out, owed)` so an `eth_call` reports the exact cycle output even
///         for unprofitable candidates.
contract ArbExecutor {
    address public owner;

    address internal constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address internal constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    struct Leg {
        address pool;       // Uniswap V3 pool (or V2 pair / router if extended)
        address tokenIn;
        address tokenOut;
        uint256 amount;     // exact input amount (or output for exact-out)
        uint160 sqrtLimit;  // sqrtPriceLimitX96
        bool zeroForOne;
    }

    error NoProfit(uint256 out, uint256 owed);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    // Called by the bot. ``arbParams`` = abi.encode(tokenIn, tokenOut, minProfit, buyLeg, sellLeg).
    function executeArbBalancer(
        address token,
        uint256 amount,
        bytes calldata arbParams
    ) external onlyOwner {
        address[] memory tokens = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        tokens[0] = token;
        amounts[0] = amount;
        IBalancerVault(BALANCER_VAULT).flashLoan(address(this), tokens, amounts, arbParams);
    }

    function executeArbAave(
        address token,
        uint256 amount,
        bytes calldata arbParams
    ) external onlyOwner {
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), token, amount, arbParams, 0);
    }

    // Balancer callback.
    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata userData
    ) external {
        require(msg.sender == BALANCER_VAULT, "not vault");
        _runCycle(tokens[0], amounts[0], feeAmounts[0], userData);
    }

    // Aave callback.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address /*initiator*/,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "not pool");
        _runCycle(asset, amount, premium, params);
        return true;
    }

    function _runCycle(address token, uint256 amount, uint256 fee, bytes calldata userData) internal {
        (address tokenOut, uint256 minProfit, Leg memory buyLeg, Leg memory sellLeg) =
            abi.decode(userData, (address, uint256, Leg, Leg));

        // 1) buy tokenOut with the borrowed token
        IERC20(buyLeg.tokenIn).approve(buyLeg.pool, buyLeg.amount);
        (int256 a0, int256 a1) = IUniswapV3Pool(buyLeg.pool).swap(
            address(this), buyLeg.zeroForOne, int256(buyLeg.amount), buyLeg.sqrtLimit, "");
        uint256 got = buyLeg.zeroForOne ? uint256(-a1) : uint256(-a0);

        // 2) sell tokenOut back into the borrowed token
        IERC20(sellLeg.tokenIn).approve(sellLeg.pool, got);
        IUniswapV3Pool(sellLeg.pool).swap(
            address(this), sellLeg.zeroForOne, int256(got), sellLeg.sqrtLimit, "");

        // 3) repay the borrowed token + fee
        uint256 owed = amount + fee;
        uint256 bal = IERC20(token).balanceOf(address(this));
        if (bal < owed + minProfit) revert NoProfit(bal, owed);
        IERC20(token).transfer(msg.sender, owed);
    }
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IBalancerVault {
    function flashLoan(address recipient, address[] calldata tokens, uint256[] calldata amounts,
                       bytes calldata userData) external;
}

interface IAavePool {
    function flashLoanSimple(address receiverAddress, address asset, uint256 amount,
                             bytes calldata params, uint16 referralCode) external;
}

interface IUniswapV3Pool {
    function swap(address recipient, bool zeroForOne, int256 amountSpecified,
                  uint160 sqrtPriceLimitX96, bytes calldata data)
        external returns (int256 amount0, int256 amount1);
}
