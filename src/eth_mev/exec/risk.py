"""Event-level risk scoring and the acceptance gate (opportunity risk).

Complements ``safety.py`` (which checks the *token* on-chain) with the *opportunity* risks read off
the strict row: unwind slippage (position size vs. pool depth), counterparty likelihood (model
score), and competition (the victim's priority fee — a victim paying a lot to land fast leaves a
short window). The gate hard-rejects unsafe tokens and rejects opportunities whose soft risks pass
a threshold.
"""
from __future__ import annotations

import os

# Thresholds, overridable via env so they can be tuned without a redeploy.
MAX_SLIPPAGE_FRAC = float(os.environ.get("MEV_MAX_SLIPPAGE", "0.05"))
MIN_COUNTERPARTY_P = float(os.environ.get("MEV_MIN_P", "0.02"))
MAX_PRIORITY_FEE_WEI = int(os.environ.get("MEV_MAX_PRIORITY_FEE", str(50_000_000_000)))  # 50 gwei


def event_risks(row: dict) -> dict:
    """The per-opportunity risks, from fields the strict screener already computes."""
    kappa = float(row.get("position_wei") or row.get("front_budget") or 0)
    depth = float(row.get("x_ref") or 0)
    p = float(row.get("p") or 0)
    prio = int(row.get("priority_fee") or 0)
    return {
        "slippage_frac": kappa / depth if depth > 0 else float("inf"),
        "counterparty_p": p,
        "priority_fee_wei": prio,
    }


def gate(token_safety: dict, risks: dict) -> tuple[bool, list[str]]:
    """Return ``(accept, reasons)``. Hard token risks and out-of-threshold soft risks reject.

    Reasons are stable category names (not the raw values) so the screener can count them cleanly.
    """
    reasons: list[str] = list(token_safety.get("risks") or [])
    if not token_safety.get("safe", False) and not reasons:
        reasons.append(token_safety.get("reason") or "unsafe_token")
    if risks["slippage_frac"] > MAX_SLIPPAGE_FRAC:
        reasons.append("slippage")
    if risks["counterparty_p"] < MIN_COUNTERPARTY_P:
        reasons.append("low_confidence")
    if risks["priority_fee_wei"] > MAX_PRIORITY_FEE_WEI:
        reasons.append("high_priority_fee")
    return not reasons, reasons
