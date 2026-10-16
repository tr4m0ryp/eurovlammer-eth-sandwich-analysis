"""Minimal aiohttp dashboard: ``/api/snapshot`` + an SSE stream for live wallet/engine state.

Presentation is chain-agnostic (the parent ``tlive/serve/*`` already proved the shape); this keeps a
read-only JSON snapshot endpoint and a simple Server-Sent-Events fan-out, so the web GUI can watch
commit/resolve events without any privileged access.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web


class Hub:
    """In-process broadcast hub: one snapshot + an SSE fan-out of stream events."""

    def __init__(self):
        self.snapshot: dict = {}
        self._subs: set[asyncio.Queue] = set()

    def update(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def publish(self, event: dict) -> None:
        payload = json.dumps(event)
        for q in list(self._subs):
            q.put_nowait(payload)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)


def build_app(hub: Hub) -> web.Application:
    app = web.Application()
    app["hub"] = hub
    app.router.add_get("/api/snapshot", snapshot_handler)
    app.router.add_get("/api/snapshot", snapshot_handler)
    app.router.add_get("/api/stream", stream_handler)
    app.router.add_get("/", index_handler)
    return app


async def snapshot_handler(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    parts = request.query.get("parts", "")
    data = hub.snapshot
    if parts:
        data = {k: v for k, v in hub.snapshot.items() if k in parts.split(",")}
    return web.json_response(data)


async def stream_handler(request: web.Request) -> web.StreamResponse:
    hub: Hub = request.app["hub"]
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q = await hub.subscribe()
    try:
        while True:
            payload = await q.get()
            await resp.write(f"data: {payload}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(q)
    return resp


async def index_handler(request: web.Request) -> web.Response:
    body = ("<html><body><h1>eth_mev screener</h1>"
            "<p><a href='/api/snapshot'>snapshot</a> · "
            "<a href='/api/stream'>SSE stream</a></p></body></html>")
    return web.Response(text=body, content_type="text/html")
