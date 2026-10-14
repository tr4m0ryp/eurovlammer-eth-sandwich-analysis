"""Which filter finds the swaps sandwichers pick? Every candidate scored on one time split.

Universe: every swap that is not itself an attacker leg and not sent by a detected bot. Label: the
swap was a victim of an entity-filtered (H3) attacker. Train = first 60% of slots, test = rest.
Two views of the features:
  attacker  pool state just before the front-run (what the attacker priced against);
  screener  the swap's own pre-state (what a post-inclusion screener sees, front-run included).

    .venv/bin/python -m research.sandwich.evaluate --labels data/interim/labels_20261014.parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from tlive.metrics.features import FEATURES, features

PHI_FREE = [f for f in FEATURES if f not in ("phi", "log_phi", "room", "log_mesp", "mesp_rel")]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
              verbose=-1, seed=7, num_threads=2)


def load(path: Path, view: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    bots = set(df.loc[df.bot & df.role.isin(["front", "back"]), "attacker"])
    df = df[df.role.isin(["", "victim"]) & ~df.signer.isin(bots)].copy()
    df["y"] = ((df.role == "victim") & df.bot).astype(int)
    if view == "screener":
        df["x_ref"], df["slack"], df["mesp"] = df.x_pre, df.slack_exec, df.mesp_exec
    df = df.sort_values(["slot", "tx_index", "ev_index"]).reset_index(drop=True)
    return pd.concat([df, features(df)], axis=1)


def topk(y: np.ndarray, s: np.ndarray, frac: float) -> tuple[float, float]:
    k = max(int(len(s) * frac), 1)
    top = np.argsort(-s, kind="stable")[:k]
    return y[top].mean(), y[top].sum() / max(y.sum(), 1)


def app_rate(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Heimbach et al.'s application-level exposure as a score: the router's train victim rate."""
    prior = train.y.mean()
    g = train.groupby("router").y.agg(["sum", "count"])
    rate = (g["sum"] + 20 * prior) / (g["count"] + 20)
    return test.router.map(rate).fillna(prior).to_numpy()


def gbm(train: pd.DataFrame, test: pd.DataFrame, cols: list[str], rounds: int = 400):
    m = lgb.train(PARAMS, lgb.Dataset(train[cols], train.y), rounds)
    return m.predict(test[cols]), m


def run(df: pd.DataFrame) -> tuple[pd.DataFrame, lgb.Booster]:
    cut = df.slot.quantile(0.6)
    tr, te = df[df.slot <= cut], df[df.slot > cut]
    y = te.y.to_numpy()
    slack = te.slack_c.where(te.slack_known == 1, te.slack_c[te.slack_known == 1].median()).to_numpy()
    scores = {
        "size (SOL)": te.log_sol.to_numpy(),
        "slippage slack": slack,
        "size x slack": te.log_sol.to_numpy() + np.log10(np.clip(slack, 1e-4, None)),
        "app exposure (router rate)": app_rate(tr, te),
        "standalone MESP": te.log_mesp.to_numpy(),
        "phi (impact / fee hurdle)": te.log_phi.to_numpy(),
        "room (slack-capped phi)": te.room.to_numpy(),
    }
    p_free, _ = gbm(tr, te, PHI_FREE)
    p_all, model = gbm(tr, te, FEATURES)
    scores["GBM without phi/MESP"] = p_free
    scores["GBM full"] = p_all
    rows = []
    for name, s in scores.items():
        p1, r1 = topk(y, s, 0.01)
        p5, r5 = topk(y, s, 0.05)
        rows.append({"score": name, "auc": roc_auc_score(y, s), "ap": average_precision_score(y, s),
                     "prec@1%": p1, "rec@1%": r1, "prec@5%": p5, "rec@5%": r5})
    res = pd.DataFrame(rows)
    res.attrs.update(n_train=len(tr), n_test=len(te), pos_train=int(tr.y.sum()), pos_test=int(y.sum()),
                     base_rate=float(y.mean()))
    return res, model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--min-sol", type=float, default=0.0)
    ap.add_argument("--save", type=Path, default=None, help="write the screener-view model here")
    a = ap.parse_args()
    pd.set_option("display.width", 200)
    for view in ("attacker", "screener"):
        df = load(a.labels, view)
        df = df[df.sol >= a.min_sol * 1e9]
        res, model = run(df)
        print(f"\n[{view}] {json.dumps(res.attrs)}")
        print(res.round(4).to_string(index=False))
        imp = pd.Series(model.feature_importance("gain"), index=FEATURES).sort_values(ascending=False)
        print("gain:", ", ".join(f"{k}={v:.0f}" for k, v in imp.head(10).items()))
        if a.save and view == "screener":
            a.save.parent.mkdir(parents=True, exist_ok=True)
            model.save_model(str(a.save))


if __name__ == "__main__":
    main()
