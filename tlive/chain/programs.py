"""Program ids, instruction discriminators and Jito tip accounts the decoders key on."""
from __future__ import annotations

import hashlib

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
SYSTEM = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
VENUES = {PUMP: "pump", PUMPSWAP: "pumpswap"}

# Jito's eight public tip accounts. Other "senders" (nozomi, 0slot, bloxroute, ...) are
# recognised by the SOL transfer pattern in tx.py, not by a hard-coded list.
JITO_TIP = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})


def disc(name: str, ns: str = "global") -> bytes:
    """Anchor discriminator: sha256("<ns>:<name>")[:8]."""
    return hashlib.sha256(f"{ns}:{name}".encode()).digest()[:8]


IX = {name: disc(name) for name in ("buy", "sell", "buy_exact_sol_in", "buy_exact_quote_in")}
EV = {name: disc(name, "event") for name in ("TradeEvent", "BuyEvent", "SellEvent")}
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")
