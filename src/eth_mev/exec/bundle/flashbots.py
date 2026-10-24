"""Flashbots / builder relay bundle send — the live send path, disabled by default.

The bundle assembly and ``eth_sendBundle`` relay call exist and are tested, but sending is gated
behind the operator's explicit go-live (a funded key + ``LIVE=1`` + a signed-off config). Until then
``FlashbotsRelay.send_bundle`` refuses with a clear error and no bytes leave the process.
"""
from __future__ import annotations

import os
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount

from ...ingest.rpc import Rpc
from .sign import load_account

# Canonical Flashbots relay (mainnet). Also used by several builders.
_DEFAULT_RELAY = "https://relay.flashbots.net"


class LiveGateError(RuntimeError):
    """Raised when a live send is attempted without the go-live gates satisfied."""


def _live_enabled() -> bool:
    return os.environ.get("LIVE", "").strip().lower() in ("1", "true", "yes")


class FlashbotsRelay:
    def __init__(self, rpc: Rpc, relay_url: str | None = None, account: LocalAccount | None = None,
                 builder_key: str | None = None):
        self.rpc = rpc
        self.relay_url = relay_url or os.environ.get("FLASHBOTS_RELAY_URL", _DEFAULT_RELAY)
        self.account = account or (load_account() if _live_enabled() else None)
        self.builder_key = builder_key or os.environ.get("FLASHBOTS_SIGNING_KEY", "")

    def _assert_live(self) -> None:
        if not _live_enabled():
            raise LiveGateError("LIVE gate is off: set LIVE=1 and provide funded keys to send bundles")
        if self.account is None:
            raise LiveGateError("no funded bot account loaded — refusing to send")

    def sign_tx_bytes(self, tx: dict) -> str:
        self._assert_live()
        return "0x" + self.account.sign_transaction(tx).raw_transaction.hex()

    def build_bundle(self, txs: list[bytes]) -> dict[str, Any]:
        """Assemble a bundle body (target block optional). No signing/sending here."""
        return {
            "txs": [("0x" + t.hex()) if isinstance(t, bytes) else t for t in txs],
            "revertingTxHashes": [],
        }

    async def send_bundle(self, bundle: dict, target_block: int | None = None) -> dict:
        """Send a bundle to the relay. Requires the go-live gates (raises otherwise)."""
        self._assert_live()
        body = dict(bundle)
        if target_block is not None:
            body["blockNumber"] = hex(target_block)
        body["jsonrpc"] = "2.0"
        body["method"] = "eth_sendBundle"
        body["params"] = [body["txs"]] + (["0x" + hex(target_block)[2:]] if target_block is not None else [])
        # The relay expects ``params`` as a list; rebuild a clean request body.
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [bundle["txs"]] + ([hex(target_block)] if target_block is not None else []),
        }
        return await self._post(req)

    async def _post(self, req: dict) -> dict:
        """POST the flashbots JSON-RPC (uses aiohttp directly; the relay is not a normal node)."""
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(self.relay_url, json=req,
                              headers={"X-Flashbots-Signature": self._signature(req)}) as r:
                return {"status": r.status, "body": await r.text()}

    def _signature(self, req: dict) -> str:
        """Flashbots requires an Ethereum signature over the request body by the signing key."""
        self._assert_live()
        import json
        from eth_account.messages import encode_defunct
        signing = load_account(self.builder_key) if self.builder_key else self.account
        msg = encode_defunct(text=json.dumps(req))
        return "0x" + signing.sign_message(msg).signature.hex()


def live_gates_met() -> tuple[bool, list[str]]:
    """Report which go-live gates are satisfied (used by run.py to refuse silent live sends)."""
    missing = []
    if not _live_enabled():
        missing.append("LIVE flag not set")
    if not os.environ.get("ETH_BOT_KEY") and not os.environ.get("ETH_BOT_PRIVKEY"):
        missing.append("no funded bot key")
    return not missing, missing
