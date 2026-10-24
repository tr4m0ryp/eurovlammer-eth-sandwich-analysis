"""Dashboard routes: page, snapshot and static assets serve; no login; traversal blocked; clean shutdown."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from eth_mev.app.dashboard.hub import Hub
from eth_mev.app.dashboard.server import build_app


async def _statuses(paths: list[str]) -> dict[str, int]:
    async with TestClient(TestServer(build_app(Hub()))) as client:
        return {p: (await client.get(p, allow_redirects=False)).status for p in paths}


def test_routes_and_clean_shutdown():
    got = asyncio.run(_statuses(["/", "/api/snapshot", "/static/css/base.css", "/login",
                                 "/static/..%2F..%2Fserver.py"]))
    assert got["/"] == 200
    assert got["/api/snapshot"] == 200
    assert got["/static/css/base.css"] == 200
    assert got["/login"] == 404
    assert got["/static/..%2F..%2Fserver.py"] == 404
