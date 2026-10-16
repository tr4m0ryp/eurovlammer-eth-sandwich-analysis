"""V3 sandwich screener web surface: passcode-gated page, JSON snapshot, and an SSE stream.

Reuses the Solana ``tlive/serve/server.py`` shape (login gate, ``/api/snapshot?parts=`` and
``/api/stream?t=``), now serving V3 opportunities from ``Hub``. Static assets are the Solana UI's CSS/
layout rethemed for V3.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from pathlib import Path

from aiohttp import web

from .hub import Hub

PAGE = Path(__file__).parent / "page"
NO_CACHE = {"Cache-Control": "no-cache"}
PARTS = {"strict": ("strict", 200), "sandwiches": ("sandwiches", 100)}

PASSCODE = os.environ.get("MEV_PASSCODE", "REDACTED")
AUTH_COOKIE = "mev_auth"
LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>
 :root{color-scheme:light;--brand:oklch(0.45 0.3132 264);--fg:oklch(0.10 0.04 264);--tertiary:oklch(0.62 0.03 264);--border:oklch(0.10 0.04 264 / 0.12)}
 *{box-sizing:border-box}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:oklch(1 0 264);color:var(--fg);font:400 14px/20px system-ui,-apple-system,"Segoe UI",sans-serif}
 form{width:min(360px,calc(100vw - 48px));display:grid;gap:14px;padding:28px;background:#fff;border:1px solid var(--border);border-radius:12px;box-shadow:0 8px 24px oklch(0.10 0.04 264 / 0.10)}
 h1{margin:0;font-size:18px;font-weight:600}
 p{margin:0;color:var(--tertiary);font-size:13px;line-height:18px}
 input{height:40px;padding:0 12px;border:1px solid var(--border);border-radius:8px;font:inherit}
 input:focus{outline:2px solid var(--brand);outline-offset:1px}
 button{height:40px;border:0;border-radius:8px;background:var(--brand);color:#fff;font:600 14px/40px system-ui,sans-serif;cursor:pointer}
 .bad{color:#c2483a;font-size:13px}
</style>
</head>
<body>
<form method="post" action="/login">
 <h1>V3 Sandwich Targets</h1>
 <p>This dashboard is locked. Enter the passcode to continue.</p>
 <input type="password" name="code" placeholder="Passcode" autofocus autocomplete="current-password">
 <button type="submit">Unlock</button>
 __BAD__
</form>
</body>
</html>"""


def _topics(req: web.Request, key: str) -> frozenset[str] | None:
    raw = req.query.get(key, "")
    return frozenset(t for t in raw.split(",") if t) or None


def build_app(hub: Hub) -> web.Application:
    token = secrets.token_urlsafe(32)

    @web.middleware
    async def auth(req: web.Request, handler):
        if req.path == "/login" or req.path.startswith("/static/"):
            return await handler(req)
        if hmac.compare_digest(req.cookies.get(AUTH_COOKIE, ""), token):
            return await handler(req)
        raise web.HTTPFound("/login")

    async def login_page(req):
        bad = '<div class="bad">Wrong passcode.</div>' if "bad" in req.query else ""
        return web.Response(text=LOGIN_HTML.replace("__BAD__", bad), content_type="text/html")

    async def login_submit(req):
        data = await req.post()
        if hmac.compare_digest(str(data.get("code", "")).strip(), PASSCODE):
            resp = web.HTTPFound("/")
            resp.set_cookie(AUTH_COOKIE, token, max_age=30 * 86400, httponly=True, samesite="Lax")
            return resp
        return web.HTTPFound("/login?bad=1")

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

    a = web.Application(middlewares=[auth])
    a.on_shutdown.append(lambda _: hub.close())
    a.router.add_get("/login", login_page)
    a.router.add_post("/login", login_submit)
    a.router.add_get("/", index)
    a.router.add_get("/api/snapshot", snapshot)
    a.router.add_get("/api/stream", stream)
    a.router.add_get("/static/{path:.+}", static)
    return a


async def serve(hub: Hub, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(build_app(hub))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
