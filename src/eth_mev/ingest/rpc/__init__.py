"""JSON-RPC access: paced HTTP calls, WebSocket subscriptions and the confirmed-head driver."""
from __future__ import annotations

from .heads import new_heads  # noqa: F401
from .http import SKIPPED, Rpc, rpc_url, ws_url  # noqa: F401
from .ws import Subscriber  # noqa: F401
