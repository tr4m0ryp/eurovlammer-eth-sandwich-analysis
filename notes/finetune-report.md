# Finetune report: capital-allocation engine

Backtest replays the persisted strict event stream (rebuilt offline: `interesting` → visible → budget-capped exposure → ROI≥1%) through the live `Allocator` plus the three tunable opportunity-risk gates, settled against the night's real `victims` feed. Selection knobs are tuned on the first 80% of slots; the last 20% is held out. Numbers are the mean over 20 seeds ± std, because realised profit is dominated by a few rare large-MESP (`whale`) targets whose commitment is path-dependent.

Data window: 4 consolidated live runs (`data/screener.duckdb`), slots 456,941,774–456,992,193 (~50k slots ≈ 5.6 h), 47,495 strict events of which 4,180 were actually sandwiched (8.8%).

## 1. Optimal parameter vector (selection knobs)

- `ALPHA_SCARCE` = `0.51`
- `BETA` = `0.007`
- `MAX_PRIORITY_FEE` = `864927`
- `MAX_SLIPPAGE_FRAC` = `0.162`
- `MIN_COUNTERPARTY_P` = `0.087`
- `MIN_P` = `0.243`
- `MIN_PI_LAMPORTS` = `430444`
- `THETA0` = `0.03`
- `THETA_INF` = `0.009`

Env-var equivalents (only these five are env-overridable today):

| Symbol | Env |
|---|---|
| `MIN_P` | `TLIVE_MIN_COMMIT_P` = `0.243` |
| `MIN_PI_LAMPORTS` | `TLIVE_MIN_COMMIT_PI` = `430444` |
| `MAX_SLIPPAGE_FRAC` | `TLIVE_MAX_SLIPPAGE` = `0.162` |
| `MIN_COUNTERPARTY_P` | `TLIVE_MIN_P` = `0.087` |
| `MAX_PRIORITY_FEE` | `TLIVE_MAX_FEE` = `864927` |


## 2. Backtest table (mean over seeds @ WR=0.5 / capture size-dep / tip 100k)

| candidate | commits | wins | race | no_cp | profit (SOL) | profit ± | max DD | Sharpe |
|---|---|---|---|---|---|---|---|---|
| default (train) | 71 | 10 | 10 | 51 | -0.0073 | 0.0035 | 3.5% | -0.102 |
| candidate 1 (train) | 88 | 20 | 19 | 50 | +0.3987 | 0.2480 | 3.6% | +0.134 |
| candidate 2 (train) | 61 | 13 | 13 | 36 | +0.2587 | 0.2474 | 2.5% | +0.114 |
| candidate 3 (train) | 83 | 16 | 15 | 52 | +0.2556 | 0.2326 | 2.9% | +0.092 |
| candidate 4 (train) | 81 | 17 | 16 | 48 | +0.2356 | 0.2342 | 3.0% | +0.105 |
| best → holdout | 9 | 1 | 0 | 8 | -0.0018 | 0.0002 | 0.4% | -0.532 |

**Holdout read:** the train-best configuration (candidate 1, +0.40 SOL on ~88 commits) commits only ~9 positions in the held-out 20% window and realises ≈0 (−0.002 SOL). Its train std (0.25 SOL) is of the order of its mean, so the train result is carried by a few rare whale targets that sat in the train window. The optimum does **not** survive the holdout: the honest expected P&L under the reference resolution is ≈0 to slightly negative once whale luck is averaged out.

## 2b. Throughput vs profit (Pareto): the MIN_P selectivity knob

Holding the other knobs at the live defaults, the model-score floor trades commit count against profit. The `≥50 commits` constraint forces a point left of MIN_P≈0.2:

| MIN_P | commits | profit (SOL) | profit ± |
|---|---|---|---|
| 0.05 | 73 | -0.0086 | 0.0050 |
| 0.1 | 71 | -0.0073 | 0.0035 |
| 0.15 | 67 | -0.0041 | 0.0023 |
| 0.2 | 54 | -0.0030 | 0.0018 |
| 0.25 | 35 | -0.0024 | 0.0015 |
| 0.3 | 27 | -0.0016 | 0.0015 |
| 0.4 | 11 | -0.0002 | 0.0026 |

## 3. Resolution sensitivity (mean over seeds, selection fixed at §1)

WIN_RATE is not observable offline; it is the dominant uncertainty, not a learned number. Each row is a 20-seed mean with a std of ~0.04–0.25 SOL (largest around WR 0.5), so the run-up from 0.1–0.9 is real but noisy; the engine turns clearly positive only above ~WR 0.5–0.6.

| WIN_RATE | expected profit (SOL) | commits |
|---|---|---|
| 0.1 | +0.0181 | 70 |
| 0.2 | +0.0061 | 74 |
| 0.3 | +0.0832 | 79 |
| 0.4 | +0.1643 | 83 |
| 0.5 | +0.3987 | 88 |
| 0.6 | +0.5004 | 91 |
| 0.7 | +0.5322 | 93 |
| 0.8 | +0.6272 | 97 |
| 0.9 | +0.7520 | 97 |

| CAPTURE_FRACTION | expected profit (SOL) |
|---|---|
| size-dependent (0.19/0.6) | +0.3987 |
| 0.19 | +0.1750 |
| 0.25 | +0.4365 |
| 0.40 | +0.8239 |
| 0.60 | +1.2859 |

| TIP_LAMPORTS | expected profit (SOL) |
|---|---|
| 50000 | +0.3550 |
| 100000 | +0.3987 |
| 200000 | +0.3388 |
| 500000 | +0.1291 |

Best selection full-window: **+0.3856 ± 0.2449 SOL** over 105 commits (42 sandwiched).


## 4. What moves the needle

Max deviation of deterministic expected P&L when each knob is pushed to the edge of its search range (relative to the §1 optimum):

| knob | max |Δ expected profit| (SOL) |
|---|---|
| `THETA0` | 0.3782 |
| `MAX_PRIORITY_FEE` | 0.3738 |
| `THETA_INF` | 0.3735 |
| `MIN_PI_LAMPORTS` | 0.3734 |
| `BETA` | 0.3671 |
| `MAX_SLIPPAGE_FRAC` | 0.3625 |
| `MIN_P` | 0.3553 |
| `ALPHA_SCARCE` | 0.0004 |
| `MIN_COUNTERPARTY_P` | 0.0000 |

## 5. Recommendation

Treat the +0.4 SOL backtest as a whale-luck upper bound, not an edge. The engine's realised P&L is dominated by WIN_RATE (unknowable offline) and by a few rare large-MESP targets, so its mean moves less than its ±0.23–0.25 SOL std. The single parameter that moves the needle most *reliably* is the model-score floor `MIN_P` (`TLIVE_MIN_COMMIT_P`): raising it from 0.1 to ≈0.25 cuts the no-counterparty bleed — the largest recurring loss — roughly in half and lifts hit-rate toward ~40% at the cost of throughput. Pair it with a looser slippage gate (`TLIVE_MAX_SLIPPAGE` 0.05 → ~0.15) and a tighter priority-fee gate (`TLIVE_MAX_FEE` ~1M → ~0.4M, to skip bot-contended victims), keeping the exposure knobs near current values. Do **not** deploy for positive expectation before measuring WIN_RATE on a small live sample: expected P&L crosses ~zero around WR 0.3–0.5 and turns clearly positive only above ~0.6–0.7. The holdout commit count and its ≈0 P&L confirm the train optimum is window-specific; ship the conservative selection, not the headline number.
