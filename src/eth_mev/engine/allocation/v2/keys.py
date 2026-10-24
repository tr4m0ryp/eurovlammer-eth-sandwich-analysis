"""Position-key formatting shared by the V2 allocator views."""
from __future__ import annotations


def fmt_key(key) -> str:
    block, tx_hash = key
    return f"{block}:{tx_hash}" if tx_hash is not None else f"b:{block}"
