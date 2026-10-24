"""Read-only views of the V2 allocator: the stats line and the dashboard snapshot."""
from __future__ import annotations

from .keys import fmt_key


class SnapshotMixin:
    def stats(self) -> dict:
        return {
            "wallet": self.wallet_eth(), "total_locked": self._eth(self.total_locked),
            "total_profit": self._eth(self.total_profit), "n_active": len(self.active),
            "n_completed": self.n_completed, "n_wins": self.n_wins,
            "n_outbid": self.n_outbid, "n_no_sandwich": self.n_no_sandwich,
            "n_sandwiched": self.n_sandwiched, "sum_realized": self._eth(self.sum_realized),
        }

    def _active_row(self, p: dict) -> dict:
        return {"key": fmt_key(p["key"]), "pool": p["pool"], "position_eth": self._eth(p["kappa"]),
                "expected_profit_eth": self._eth(p["pi"]), "roi": p["roi"],
                "commit_block": p["commit_block"], "unlock_block": p["unlock_block"],
                "seen_ms": p["seen_ms"], "status": "locked"}

    def _history_row(self, h: dict) -> dict:
        return {"key": fmt_key(h["key"]), "pool": h["pool"], "position_eth": self._eth(h["kappa"]),
                "expected_profit_eth": self._eth(h["pi"]), "gain_eth": self._eth(h["gain"]),
                "status": h["status"], "commit_block": h["commit_block"],
                "unlock_block": h["unlock_block"], "seen_ms": h["seen_ms"],
                "resolved_ms": h["resolved_ms"]}

    def state(self) -> dict:
        """The full wallet state for ``/api/snapshot`` and the artifact body."""
        positions = sorted(self.active.values(), key=lambda p: p["commit_block"], reverse=True)
        return {
            "wallet": self.wallet_eth(), "total_locked": self._eth(self.total_locked),
            "total_profit": self._eth(self.total_profit), "n_active": len(self.active),
            "n_completed": self.n_completed, "n_wins": self.n_wins,
            "n_outbid": self.n_outbid, "n_no_sandwich": self.n_no_sandwich,
            "n_sandwiched": self.n_sandwiched, "sum_realized": self._eth(self.sum_realized),
            "positions": [self._active_row(p) for p in positions],
            "history": [self._history_row(h) for h in reversed(self.history[-200:])],
        }
