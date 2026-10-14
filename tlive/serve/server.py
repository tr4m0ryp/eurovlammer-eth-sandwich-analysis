"""Local web surface: the page, a JSON snapshot, and a server-sent event stream.

The hub is the only shared state: the pipeline writes into it, the HTTP handlers read from it.
Events are small JSON objects (``flag``, ``settle``, ``sandwich``, ``stats``) fanned out to every
open stream; a slow client drops events rather than holding the pipeline.
"""
from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import orjson
from aiohttp import web

PAGE = Path(__file__).parent / "page"
RECENT = 600
SNAP_FLAGS = 120
SNAP_RISK = 120
SNAP_SANDS = 50
SNAP_VISIBLE = 120


class Hub:
    def __init__(self):
        self.flags: deque[dict] = deque(maxlen=RECENT)
        self.sandwiches: deque[dict] = deque(maxlen=200)
        self.risk: deque[dict] = deque(maxlen=RECENT)
        self.visible_optimal: deque[dict] = deque(maxlen=200)
        self.stats: dict = {}
        self.by_key: dict[tuple[str, int], dict] = {}
        self.subs: set[asyncio.Queue] = set()

    def publish(self, kind: str, data) -> None:
        msg = orjson.dumps({"t": kind, "d": data})
        for q in list(self.subs):
            if q.qsize() < 2000:
                q.put_nowait(msg)

    def add_flag(self, row: dict) -> None:
        if len(self.flags) == self.flags.maxlen:
            old = self.flags[0]
            self.by_key.pop((old["signature"], old["ev_index"]), None)
        self.flags.append(row)
        self.by_key[(row["signature"], row["ev_index"])] = row
        self.publish("flag", row)

    def add_risk(self, rows: list[dict]) -> None:
        if rows:
            self.risk.extend(rows)
            self.publish("risk", rows)

    def settle(self, slot: int, victims: dict) -> None:
        changed = []
        for row in self.flags:
            if row["status"] != "pending" or row["slot"] > slot:
                continue
            hit = victims.get((row["signature"], row["ev_index"]))
            if hit:
                row.update(status="sandwiched", attacker=hit[1], sw_profit=hit[2])
            else:
                row["status"] = "clean"
            changed.append({k: row[k] for k in ("signature", "ev_index", "status", "attacker", "sw_profit")})
        if changed:
            self.publish("settle", changed)


def app(hub: Hub) -> web.Application:
    async def index(_):
        return web.FileResponse(PAGE / "index.html")

    async def snapshot(_):
        flags = list(hub.flags)[-SNAP_FLAGS:]
        sands = list(hub.sandwiches)[-SNAP_SANDS:]
        risk = list(hub.risk)[-SNAP_RISK:]
        visible = list(hub.visible_optimal)[-SNAP_VISIBLE:]
        body = orjson.dumps({"stats": hub.stats, "flags": flags, "sandwiches": sands,
                             "risk": risk, "visible": visible},
                            option=orjson.OPT_SERIALIZE_NUMPY)
        return web.Response(body=body, content_type="application/json")

    async def stream(req):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(req)
        q: asyncio.Queue = asyncio.Queue()
        hub.subs.add(q)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                    await resp.write(b"data: " + msg + b"\n\n")
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            hub.subs.discard(q)
        return resp

    a = web.Application()
    a.router.add_get("/", index)
    a.router.add_get("/api/snapshot", snapshot)
    a.router.add_get("/api/stream", stream)
    a.router.add_static("/static", PAGE)
    return a


async def serve(hub: Hub, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app(hub))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
