"""The live incremental context must equal the backtest's vectorised context, row for row."""
import gzip
from pathlib import Path

import numpy as np
import orjson
import pandas as pd
import pytest

from tlive.ingest.follow import decode_block
from tlive.metrics.features import context_features
from tlive.screen.scorer import Context

RAW = sorted(Path("data/raw/blocks_20261014").glob("*.jsonl.gz"))


@pytest.mark.skipif(not RAW, reason="no pulled blocks on this machine")
def test_incremental_context_matches_backtest():
    ctx, frames = Context(), []
    with gzip.open(RAW[-1], "rb") as fh:
        for n, line in enumerate(fh):
            if n == 60:
                break
            blk = orjson.loads(line)
            raw = orjson.dumps({"result": {"blockTime": blk["block_time"], "transactions": blk["txs"]}})
            df = pd.DataFrame(decode_block(raw, blk["slot"])["swaps"])
            if df.empty:
                continue
            df = df.sort_values(["tx_index", "ev_index"]).reset_index(drop=True)
            frames.append(pd.concat([df, ctx.features(df)], axis=1))
    live = pd.concat(frames, ignore_index=True)
    ref = context_features(live.drop(columns=["log_flow_rel", "log_pool_n", "log_wallet_n"]))
    for c in ("log_flow_rel", "log_pool_n", "log_wallet_n"):
        np.testing.assert_allclose(live[c].to_numpy(), ref[c].to_numpy(), rtol=1e-9, atol=1e-12)
