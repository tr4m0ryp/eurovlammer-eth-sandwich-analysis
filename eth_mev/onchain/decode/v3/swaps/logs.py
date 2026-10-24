"""Decoding of V3 ``Swap`` event logs into ``V3Swap`` records with the victim's guard attached."""
from __future__ import annotations

from eth_abi import decode as abi_decode

from ..constants import V3_SWAP_EVENT_TOPIC
from .model import V3Swap


def decode_v3_swap_logs(tx_hash: str, block_number: int | None, tx_index: int, logs: list[dict],
                         calldata: dict | None = None,
                         token_by_pool: dict[str, tuple[str, str, int]] | None = None) -> list[V3Swap]:
    """Decode V3 ``Swap`` events in ``logs`` into ``V3Swap`` records, attaching the victim's guard."""
    out: list[V3Swap] = []
    tbp = token_by_pool or {}
    for i, lg in enumerate(logs):
        if not isinstance(lg, dict):
            continue
        topics = lg.get("topics") or []
        if not topics or topics[0].lower() != V3_SWAP_EVENT_TOPIC or lg.get("address") is None:
            continue
        pool = lg["address"]
        t0, t1, fee = tbp.get(pool, ("", "", 0))
        data = bytes.fromhex((lg.get("data") or "0x")[2:].replace("0x", ""))
        try:
                    amount0, amount1, sqrt_price, liquidity, tick = abi_decode(
                ["int256", "int256", "uint160", "uint128", "int24"], data)
        except Exception:
            continue
        # The Swap event's sender is topics[1]; topics[2] is the recipient.
        sender = topics[1] if len(topics) > 1 else None
        out.append(V3Swap(
            tx_hash=tx_hash, block_number=block_number, tx_index=tx_index, log_index=i,
            pool=pool, token0=t0, token1=t1, fee=int(fee), user="",
            amount0=int(amount0), amount1=int(amount1),
            sqrt_price_x96=int(sqrt_price), liquidity=int(liquidity), tick=int(tick),
            amount_out_min=calldata.get("amount_out_min") if calldata else None,
            amount_in=calldata.get("amount_in") if calldata else None,
            deadline=calldata.get("deadline") if calldata else None,
            raw={"sender": sender, "n_hops": (calldata.get("n_hops") if calldata else None)},
        ))
    return out
