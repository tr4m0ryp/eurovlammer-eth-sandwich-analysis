"""V3 sandwich allocator — flash-loan front (borrow the front, bankroll covers only costs).

A flash loan is borrowed and repaid atomically inside the same bundle, so the *front* is no longer
bounded by the bankroll — it is bounded by the victim's slippage room and the loan amount. The
bankroll (``MEV_BANKROLL_ETH``) becomes a **cost buffer** that funds gas + bribe + the flash-loan fee,
and accumulates profit. This is the step change that lets 0.09 ETH drive a 10 ETH front.

Risk model (the new filters):
- **flash fee** scales with the front: ``front × FLASH_FEE_BPS / 10_000`` (Aave ~0.05%).
- **slack floor** (``MIN_SLACK``): a victim whose guard slack is below this is rejected — too little
  room to enter/exit a flash-loan position without reverting.
- **expected break-even**: commit iff ``mesp > cost / (WIN_RATE × CAPTURE_FRACTION)`` where
  ``cost = gas + bribe + flash_fee(front)``, consistent with the resolve.

Resolution is expected-value (no RNG): ``E[gain] = WIN_RATE × CAPTURE × mesp − cost``. The loan is
returned atomically, so only the cost and profit touch the bankroll.
"""
from __future__ import annotations

import os
import time

WEI = 10 ** 18


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class V3Allocator:
    # Tunables (env-overridable).
    BANKROLL_ETH = float(_env("MEV_BANKROLL_ETH", "0.09"))     # cost buffer + profit accumulator
    FLASH_LOAN_ETH = float(_env("MEV_FLASH_LOAN_ETH", "10"))   # loaned front (0 = no flash loan)
    FLASH_FEE_BPS = int(_env("MEV_FLASH_FEE_BPS", "5"))        # flash-loan fee (5 bps = 0.05%)
    GAS_GWEI = float(_env("MEV_GAS_GWEI", "0.2"))              # base+priority fee per gas (gwei)
    SWAP_GAS = 160_000                                          # gas per V3 swap; 2 swaps + borrow/repay
    BRIBE_WEI = int(_env("MEV_BRIBE_WEI", str(int(0.0002 * WEI))))
    WIN_RATE = float(_env("MEV_WIN_RATE", "0.5"))
    CAPTURE_FRACTION = float(_env("MEV_CAPTURE_FRACTION", "0.25"))
    RESOLVE_BLOCKS = int(_env("MEV_RESOLVE_BLOCKS", "3"))
    RISK_APPETITE = float(_env("MEV_RISK_APPETITE", "1.0"))
    MIN_SLACK = float(_env("MEV_MIN_SLACK", "0.001"))          # 0.1% floor (borrow/repay slippage margin)
    HISTORY_MAX = 500

    def __init__(self, bankroll_eth: float | None = None):
        br = bankroll_eth if bankroll_eth is not None else self.BANKROLL_ETH
        self.bankroll = int(br * WEI)
        self.wallet = self.bankroll
        self.loan_wei = int(self.FLASH_LOAN_ETH * WEI)
        self.total_locked = 0          # total borrowed (informational)
        self.total_profit = 0          # signed wei
        self.positions: dict[tuple, dict] = {}
        self.history: list[dict] = []
        self.n_committed = self.n_resolved = 0
        self.n_wins = self.n_losses = 0

    # ---- cost model ----
    @property
    def gas_cost_wei(self) -> int:
        # flash loan adds a borrow + repay leg (~2 more swaps); no flash loan = 2 swaps
        legs = 4 if self.FLASH_LOAN_ETH > 0 else 2
        return int(legs * self.SWAP_GAS * self.GAS_GWEI * 1e9)

    def flash_fee_wei(self, front: int) -> int:
        return int(front * self.FLASH_FEE_BPS / 10_000)

    def landing_cost_wei(self, front: int | None = None) -> int:
        """Total fixed cost of landing a sandwich; the flash fee scales with the borrowed front."""
        cost = self.gas_cost_wei + self.BRIBE_WEI
        if self.FLASH_LOAN_ETH > 0 and front is not None:
            cost += self.flash_fee_wei(front)
        return cost

    # ---- decision rule ----
    def commit_threshold_wei(self, front: int | None = None) -> int:
        """MESP a target must exceed for its *expected* gain to clear the landing cost."""
        denom = self.WIN_RATE * self.CAPTURE_FRACTION * self.RISK_APPETITE
        if denom <= 0:
            return int(1e30)
        return int(self.landing_cost_wei(front) / denom)

    def decide(self, row: dict) -> bool:
        mesp = int(row.get("mesp") or 0)
        front = int(row.get("front") or 0)
        if mesp <= 0 or front <= 0:
            return False
        if self.FLASH_LOAN_ETH > 0:
            # flash loan: front is borrowed; only the slack floor and cost buffer bind
            slack = row.get("slack")
            if slack is None or not (self.MIN_SLACK <= slack < 100.0):
                return False
            if self.landing_cost_wei(front) > self.wallet:
                return False                    # cost buffer exhausted
        else:
            # no flash loan: the front must fit the available capital
            if front > self.wallet:
                return False
        if mesp <= self.commit_threshold_wei(front if self.FLASH_LOAN_ETH > 0 else None):
            return False
        return True

    def _expected_gain(self, mesp: int, cost: int) -> int:
        win_gain = int(self.CAPTURE_FRACTION * mesp) - cost
        loss_gain = -cost
        return int(self.WIN_RATE * win_gain + (1 - self.WIN_RATE) * loss_gain)

    # ---- position lifecycle ----
    def _commit(self, row: dict, block: int) -> dict | None:
        front = int(row.get("front") or 0)
        mesp = int(row.get("mesp") or 0)
        if front <= 0 or mesp <= 0:
            return None
        if self.FLASH_LOAN_ETH > 0:
            front = min(front, self.loan_wei)
            cost = self.landing_cost_wei(front)
            if cost > self.wallet:
                return None                 # cost buffer exhausted
            self.total_locked += front      # borrowed (informational), bankroll untouched
        else:
            cost = self.landing_cost_wei()
            if front > self.wallet:
                return None                 # capital constraint (no flash loan)
            self.wallet -= front
            self.total_locked += front
        key = (block, row.get("tx_hash") or "")
        self.positions[key] = {
            "key": key, "pool": row.get("pool", ""), "front": front, "mesp": mesp,
            "cost": cost, "roi": row.get("roi"), "slack": row.get("slack"), "block": block,
            "unlock_block": block + self.RESOLVE_BLOCKS,
            "seen_ms": row.get("seen_ms", int(time.time() * 1000)),
            "net_expected": self._expected_gain(mesp, cost),
        }
        self.n_committed += 1
        return self.positions[key]

    def _resolve(self, block: int) -> list[dict]:
        due = [k for k, p in self.positions.items() if p["unlock_block"] <= block]
        events = []
        for key in due:
            pos = self.positions.pop(key)
            gain = self._expected_gain(pos["mesp"], pos["cost"])
            if self.FLASH_LOAN_ETH > 0:
                self.wallet += gain             # loan returned atomically; only P&L lands
            else:
                self.wallet += pos["front"] + gain
            self.total_locked -= pos["front"]
            self.total_profit += gain
            self.n_resolved += 1
            if gain > 0:
                self.n_wins += 1
            else:
                self.n_losses += 1
            entry = {**pos, "gain": gain, "resolved_block": block,
                     "resolved_ms": int(time.time() * 1000)}
            self.history.append(entry)
            del self.history[:-self.HISTORY_MAX]
            events.append(entry)
        return events

    def step(self, block: int, rows: list[dict] | None = None) -> list[dict]:
        events = self._resolve(block)
        accepted = [r for r in (rows or ()) if self.decide(r)]
        accepted.sort(key=lambda r: r.get("mesp") or 0, reverse=True)
        for row in accepted:
            pos = self._commit(row, block)
            if pos:
                events.append({"type": "commit", **pos})
        return events

    # ---- snapshot ----
    def stats(self) -> dict:
        return {
            "wallet": self.wallet / WEI,
            "bankroll": self.bankroll / WEI,
            "flash_loan": self.FLASH_LOAN_ETH,
            "flash_fee_bps": self.FLASH_FEE_BPS,
            "total_locked": self.total_locked / WEI,
            "total_profit": self.total_profit / WEI,
            "n_active": len(self.positions),
            "n_committed": self.n_committed,
            "n_resolved": self.n_resolved,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "gas_cost": self.gas_cost_wei / WEI,
            "bribe": self.BRIBE_WEI / WEI,
            "landing_cost": self.landing_cost_wei() / WEI,
            "commit_threshold": self.commit_threshold_wei() / WEI,
            "risk_appetite": self.RISK_APPETITE,
        }

    def state(self) -> dict:
        active = sorted(self.positions.values(), key=lambda p: -p["block"])
        hist = sorted(self.history, key=lambda h: -h["resolved_block"])[:200]
        return {
            "stats": self.stats(),
            "active": [{
                "pool": p["pool"], "front": p["front"] / WEI, "mesp": p["mesp"] / WEI,
                "net_expected": p["net_expected"] / WEI, "cost": p["cost"] / WEI,
                "block": p["block"], "unlock_block": p["unlock_block"], "seen_ms": p["seen_ms"],
            } for p in active],
            "history": [{
                "pool": h["pool"], "front": h["front"] / WEI, "mesp": h["mesp"] / WEI,
                "gain": h["gain"] / WEI, "cost": h["cost"] / WEI,
                "block": h["block"], "resolved_block": h["resolved_block"],
            } for h in hist],
        }
