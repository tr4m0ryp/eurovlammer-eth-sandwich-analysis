"""Confirmed-block driver shared by the block-level screeners: one ``newHeads`` header at a time."""
from __future__ import annotations

import time
from typing import AsyncIterator

from .http import rpc_url, ws_url
from .ws import Subscriber


async def new_heads(hours: float) -> AsyncIterator[dict]:
    """Yield each well-formed ``newHeads`` header until ``hours`` elapse.

    Malformed or error frames are skipped; the yielded header always carries an int ``number``."""
    deadline = time.monotonic() + hours * 3600
    sub = Subscriber(ws_url() or rpc_url().replace("https://", "wss://"), ["newHeads"])
    try:
        async for msg in sub.messages():
            if time.monotonic() > deadline:
                break
            if "error" in msg or "params" not in msg:
                continue
            params = msg.get("params")
            if not isinstance(params, dict):
                continue
            payload = params.get("result")
            if not (isinstance(payload, dict) and "number" in payload):
                continue
            try:
                number = int(payload["number"], 16)
            except (TypeError, ValueError):
                continue
            yield {**payload, "number": number}
    finally:
        await sub.close()
