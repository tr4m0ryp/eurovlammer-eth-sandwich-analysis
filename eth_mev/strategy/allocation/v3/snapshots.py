"""Read-only views of the V3 allocator: the stats line and the dashboard snapshot."""
from __future__ import annotations

from ..units import WEI


class SnapshotMixin:
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
