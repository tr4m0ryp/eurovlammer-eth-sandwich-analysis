"""Local web surface: the page, a JSON snapshot, the study documents, and a server-sent event stream.

Both the snapshot and the stream take a topic list, so a page only pays for what it renders:
``/api/snapshot?parts=stats,strict`` and ``/api/stream?t=stats,strict``. Without one they send all.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import orjson
from aiohttp import web

from .hub import Hub

PAGE = Path(__file__).parent / "page"
DOCS = Path(__file__).resolve().parents[2] / "data" / "artifact"
DOC_ID = re.compile(r"(optimal|visibility|exposure)/(summary|rows_\d{3})")
NO_CACHE = {"Cache-Control": "no-cache"}     # the page changes under a running screener; always revalidate
PARTS = {"flags": ("flags", 120), "sandwiches": ("sandwiches", 50), "risk": ("risk", 120),
         "visible": ("visible_optimal", 120), "strict": ("strict_optimal", 120)}


def _topics(req: web.Request, key: str) -> frozenset[str] | None:
    raw = req.query.get(key, "")
    return frozenset(t for t in raw.split(",") if t) or None


async def _close(hub: Hub) -> None:
    hub.close()


def app(hub: Hub) -> web.Application:
    async def index(_):
        return web.FileResponse(PAGE / "index.html", headers=NO_CACHE)

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
        return web.Response(body=orjson.dumps(body, option=orjson.OPT_SERIALIZE_NUMPY),
                            content_type="application/json", headers=NO_CACHE)

    async def doc(req):
        """The study documents the artifact reads from its database, served from disk."""
        doc_id = req.match_info["id"]
        path = DOCS / f"{doc_id}.json"
        if not DOC_ID.fullmatch(doc_id) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "application/json", **NO_CACHE})

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
                while msgs[-1] is not None and not q.empty():    # drain a backlog in one write
                    msgs.append(q.get_nowait())
                data = b"".join(b"data: " + m + b"\n\n" for m in msgs if m is not None)
                if data:
                    await resp.write(data)
                if msgs[-1] is None:             # hub closed: the screener is shutting down
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            hub.unsubscribe(q)
        return resp

    a = web.Application()
    a.on_shutdown.append(lambda _: _close(hub))
    a.router.add_get("/", index)
    a.router.add_get("/api/snapshot", snapshot)
    a.router.add_get("/api/stream", stream)
    a.router.add_get("/api/doc/{id:.+}", doc)
    a.router.add_get("/static/{path:.+}", static)
    return a


async def serve(hub: Hub, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app(hub))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
