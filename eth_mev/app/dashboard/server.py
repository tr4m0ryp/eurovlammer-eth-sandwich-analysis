"""Screener web surface: the dashboard page, a JSON snapshot and an SSE stream.

Reuses the Solana ``tlive/serve/server.py`` shape (``/api/snapshot?parts=`` and ``/api/stream?t=``),
serving opportunities from ``Hub``. It is a local analysis view with no access control, so the
screeners bind it to localhost. Static assets are the Solana UI's CSS/
layout rethemed for V3.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web

from .hub import Hub

PAGE = Path(__file__).parent / "page"
NO_CACHE = {"Cache-Control": "no-cache"}
PARTS = {"strict": ("strict", 200), "sandwiches": ("sandwiches", 100)}

def _topics(req: web.Request, key: str) -> frozenset[str] | None:
    raw = req.query.get(key, "")
    return frozenset(t for t in raw.split(",") if t) or None


def build_app(hub: Hub, view: str = "v3") -> web.Application:
    """Serve ``views/<view>.html`` at ``/`` (``v3`` or ``arb``) plus the shared API and assets."""
    page = PAGE / "views" / f"{view}.html"
    if not page.is_file():
        raise ValueError(f"unknown dashboard view {view!r}")
    async def index(_):
        return web.FileResponse(page, headers=NO_CACHE)

    async def static(req):
        path = (PAGE / req.match_info["path"]).resolve()
        if not path.is_relative_to(PAGE.resolve()) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers=NO_CACHE)

    async def snapshot(req):
        want = _topics(req, "parts")
        body = {"stats": hub.stats} if want is None or "stats" in want else {}
        for part, (attr, n) in PARTS.items():
            if want is None or part in want:
                body[part] = list(getattr(hub, attr))[-n:]
        if want is None or "wallet" in want:
            body["wallet"] = hub.wallet
        return web.Response(body=json.dumps(body).encode(),
                            content_type="application/json", headers=NO_CACHE)

    async def stream(req):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", **NO_CACHE})
        await resp.prepare(req)
        q = hub.subscribe(_topics(req, "t"))
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
                msgs = [msg]
                while msgs[-1] is not None and not q.empty():
                    msgs.append(q.get_nowait())
                data = b"".join(b"data: " + m + b"\n\n" for m in msgs if m is not None)
                if data:
                    await resp.write(data)
                if msgs[-1] is None:
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            hub.unsubscribe(q)
        return resp

    async def close_hub(_app: web.Application) -> None:
        hub.close()

    a = web.Application()
    a.on_shutdown.append(close_hub)
    a.router.add_get("/", index)
    a.router.add_get("/api/snapshot", snapshot)
    a.router.add_get("/api/stream", stream)
    a.router.add_get("/static/{path:.+}", static)
    return a


async def serve(hub: Hub, host: str, port: int, view: str = "v3") -> web.AppRunner:
    runner = web.AppRunner(build_app(hub, view))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
