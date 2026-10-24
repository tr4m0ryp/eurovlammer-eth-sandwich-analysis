"""Third-party router decoding: aggregators (1inch, 0x, ParaSwap) and private MEV-bot routers."""
from __future__ import annotations

from .aggregators import decode_aggregator  # noqa: F401
from .private import decode_private_router  # noqa: F401
