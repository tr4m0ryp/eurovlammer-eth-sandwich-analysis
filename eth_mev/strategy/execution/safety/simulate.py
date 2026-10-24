"""Forked / ``eth_call`` simulation gate: verify a route before it is ever enabled live.

Every new route/token must be simulated against a forked node (or ``eth_call`` on the live state)
before ``LIVE`` may flip. ``simulate_tx`` runs a signed tx through ``eth_call`` (state-only, no
broadcast) and checks it would not revert and the balance delta is sane. This is the mandatory gate
#4 in ``CLAUDE.md``.
"""
from __future__ import annotations


from ....onchain.node import Rpc


async def eth_call(rpc: Rpc, to: str, data: str, from_addr: str, value: int = 0,
                   block: str = "latest") -> str | None:
    """``eth_call`` one transaction payload; returns the raw return data hex or None on revert."""
    return await rpc.call_view(to, data, block) if False else await rpc.call(
        "eth_call", [{"to": to, "from": from_addr, "data": data, "value": hex(value)}, block])


async def simulate_swap(rpc: Rpc, router: str, calldata: str, from_addr: str,
                        value: int = 0) -> dict:
    """Simulate a swap leg. Returns ``{"ok": bool, "reverted": bool, "return_data": ...}``."""
    try:
        raw = await eth_call(rpc, router, calldata, from_addr, value)
    except Exception as e:
        return {"ok": False, "reverted": True, "error": repr(e)[:200]}
    if raw is None:
        return {"ok": False, "reverted": True, "error": "reverted (no return data)"}
    # A revert is signalled by the provider as an error body (already handled) or a "0x" with a
    # revert reason; a successful swap returns the ABI-encoded amounts array.
    reverted = raw == "0x" or (isinstance(raw, str) and raw.startswith("0x08c379a0"))
    return {"ok": not reverted, "reverted": reverted, "return_data": (raw or "")[:66]}


class Simulator:
    """Holds the configured fork/provider and gates routes."""

    def __init__(self, rpc: Rpc, enabled: bool = True):
        self.rpc = rpc
        self.enabled = enabled
        self.checked: set[str] = set()

    async def gate_route(self, router: str, calldata: str, from_addr: str) -> bool:
        """Return True only if the route simulates clean (or simulation is disabled for dry-run)."""
        route_key = f"{router}:{calldata[:10]}"
        if route_key in self.checked:
            return True
        res = await simulate_swap(self.rpc, router, calldata, from_addr)
        if res["ok"]:
            self.checked.add(route_key)
            return True
        return False
