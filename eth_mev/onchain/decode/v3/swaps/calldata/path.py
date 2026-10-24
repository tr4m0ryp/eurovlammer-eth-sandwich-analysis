"""Calldata primitives shared by the V3 decoders: selector extraction and ``bytes path`` hops."""
from __future__ import annotations

def selector(data: bytes) -> str:
    return "0x" + data[:4].hex() if len(data) >= 4 else ""


def decode_path(path_bytes: bytes) -> list[tuple[str, str, int]]:
    """Decode a V3 ``bytes path`` into hop list [(token_in, token_out, fee), ...].

    Each hop is 20-byte tokenIn + 3-byte fee + 20-byte tokenOut. A path has >= 1 hop."""
    hops = []
    i = 0
    while i + 20 + 3 + 20 <= len(path_bytes):
        token_in = "0x" + path_bytes[i:i + 20].hex()
        fee = int.from_bytes(path_bytes[i + 20:i + 23], "big")
        token_out = "0x" + path_bytes[i + 23:i + 43].hex()
        hops.append((token_in, token_out, fee))
        i += 23
    return hops
