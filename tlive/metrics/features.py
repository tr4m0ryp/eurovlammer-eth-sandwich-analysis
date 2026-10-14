"""Per-swap features of sandwich attractiveness, vectorised over a frame of swaps.

The one idea that makes fees comparable across pools: an attacker who round-trips through a pool
pays its fee twice, so a swap only pays for a sandwich when the price move it causes beats the
round-trip hurdle. Both sides of that comparison are exact on a constant-product curve:

    impact  i = (1 + n/x)^2 - 1                 (buy: n net lamports into SOL reserve x)
              = 1 - (1 - o/x)^2                 (sell: o lamports out)
    hurdle  h = (1 + r)/(1 - r) - 1             (r = combined fee rate, both legs)
    phi       = i / h                           (> 1: the swap alone can carry a sandwich)

``phi`` folds pool depth, trade size and the pool's own fee into one number, so a 0.3 SOL buy is
dull on a 90 SOL, 120 bps pool (phi ~ 0.3) and prime on a 5 SOL, 30 bps one (phi ~ 20). The
victim's limit then caps how far the price may be pushed: ``room = min(slack, i)`` relative to h.
Rolling context (pool flow and wallet activity over the last ``CTX_SLOTS``) is computed by the
caller from strictly earlier swaps, so the same code serves the backtest and the live screener.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CTX_SLOTS = 8
WALLET_SLOTS = 150
POOL_HIST_SLOTS = 600        # attack history window on a pool
SETTLE_SLOTS = 14            # a sandwich is known only this long after its front leg (live labeller)
SLACK_CAP = 10.0
FEATURES = ["log_sol", "log_x", "f_fee_bps", "phi", "log_phi", "slack_c", "slack_known", "room",
            "log_mesp", "mesp_rel", "f_buy", "exact_out", "venue_swap", "direct", "f_dontfront",
            "log_cu_price", "has_tip", "log_flow_rel", "log_pool_n", "log_wallet_n", "log_pool_hist"]


def base_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features that need only the swap and its pool state (``x_ref``, ``sol``, ``fee_bps``...)."""
    out = pd.DataFrame(index=df.index)
    x = df["x_ref"].astype(float).clip(lower=1.0)
    n = df["sol"].astype(float)
    r = df["fee_bps"].astype(float) / 1e4
    buy = df["is_buy"].astype(bool)
    impact = np.where(buy, (1 + n / x) ** 2 - 1, 1 - (1 - (n / x).clip(upper=0.999)) ** 2)
    hurdle = ((1 + r) / (1 - r) - 1).clip(lower=1e-4)    # fee-free pools: a tip/gas-scale floor
    slack = df["slack"].astype(float)
    known = np.isfinite(slack) | np.isinf(slack)
    slack_c = slack.where(known, np.nan).clip(upper=SLACK_CAP)
    out["log_sol"] = np.log10(n.clip(lower=1) / 1e9)
    out["log_x"] = np.log10(x / 1e9)
    out["f_fee_bps"] = df["fee_bps"].astype(float)
    out["phi"] = impact / hurdle
    out["log_phi"] = np.log10(out["phi"].clip(lower=1e-6))
    out["slack_c"] = slack_c.fillna(-1.0)
    out["slack_known"] = known.astype(int)
    room = np.minimum(slack_c.fillna(SLACK_CAP).clip(lower=0), impact) / hurdle
    out["room"] = room
    out["log_mesp"] = np.log10(df["mesp"].astype(float).clip(lower=1) / 1e9)
    out["mesp_rel"] = df["mesp"].astype(float) / n.clip(lower=1)
    out["f_buy"] = buy.astype(int)
    out["exact_out"] = (df["kind"] == "out").astype(int)
    out["venue_swap"] = (df["venue"] == "pumpswap").astype(int)
    out["direct"] = (df["router"] == "").astype(int)
    out["f_dontfront"] = df["dontfront"].astype(int) if "dontfront" in df else 0
    out["log_cu_price"] = np.log10(df["cu_price"].astype(float).fillna(0) + 1)
    out["has_tip"] = (df["jito_tip"].astype(float) > 0).astype(int)
    return out


def context_features(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling context from strictly earlier swaps: same-side pool flow over the last CTX_SLOTS
    (relative to the pool's SOL reserve), pool trade count, and the signer's own trade count
    (scalpers trade constantly; a first-time buyer does not). ``df`` must be in chain order."""
    out = pd.DataFrame(index=df.index)
    slot = df["slot"].to_numpy()
    flow = np.zeros(len(df))
    pool_n = np.zeros(len(df))
    wallet_n = np.zeros(len(df))
    for _, idx in df.groupby("pool", sort=False).indices.items():
        _rolling(slot[idx], df["sol"].to_numpy()[idx] * np.where(df["is_buy"].to_numpy()[idx], 1, -1),
                 df["is_buy"].to_numpy()[idx], idx, flow, pool_n)
    for _, idx in df.groupby("signer", sort=False).indices.items():
        s = slot[idx]
        lo = np.searchsorted(s, s - WALLET_SLOTS, side="left")   # the wallet's recent history
        wallet_n[idx] = np.arange(len(idx)) - lo
    x = df["x_ref"].astype(float).clip(lower=1.0).to_numpy()
    out["log_flow_rel"] = np.log10(np.abs(flow) / x + 1e-6)
    out["log_pool_n"] = np.log10(pool_n + 1)
    out["log_wallet_n"] = np.log10(wallet_n + 1)
    return out


def _rolling(slot, signed, is_buy, idx, flow, count):
    """Same-side signed SOL flow and trade count over the previous CTX_SLOTS, current excluded."""
    csum = np.concatenate([[0.0], np.cumsum(signed)])
    lo = np.searchsorted(slot, slot - CTX_SLOTS, side="left")
    pos = np.arange(len(slot))
    window = csum[pos] - csum[lo]
    flow[idx] = np.where(is_buy, window, -window)
    count[idx] = pos - lo


def pool_history(df: pd.DataFrame, fronts: dict[str, np.ndarray]) -> pd.Series:
    """Sandwiches already settled on the swap's pool: fronts in [slot - POOL_HIST_SLOTS,
    slot - SETTLE_SLOTS). ``fronts`` maps pool -> sorted front-leg slots. Attackers camp on pools,
    so this is the single strongest feature, and the settle lag keeps it point-in-time."""
    cnt = np.zeros(len(df))
    slot = df["slot"].to_numpy()
    for pool, idx in df.groupby("pool", sort=False).indices.items():
        a = fronts.get(pool)
        if a is not None and len(a):
            s = slot[idx]
            cnt[idx] = np.searchsorted(a, s - SETTLE_SLOTS, "left") - np.searchsorted(a, s - POOL_HIST_SLOTS, "left")
    return pd.Series(np.log10(cnt + 1), index=df.index, name="log_pool_hist")


def features(df: pd.DataFrame, fronts: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
    hist = pool_history(df, fronts or {})
    return pd.concat([base_features(df), context_features(df), hist], axis=1)[FEATURES]
