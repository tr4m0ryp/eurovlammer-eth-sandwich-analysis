# Optimal Capital Allocation Under Stochastic Opportunity Arrival

## Problem Statement

We have a logged event stream from a live distributed-ledger block processor. Each event describes a detected price asymmetry on a constant-product liquidity pool (CPMM). The stream is ordered by a discrete slot counter (τ, ~400ms tick) and was captured from real mainnet traffic over a 30-minute window.

The dataset contains ~60 events that passed a multi-gate filter (see: `strict_screener_output`). We want to design a mathematical framework for optimal capital allocation across this opportunity stream under a hard capital constraint.

---

## Dataset Schema

Each logged opportunity `e_i` was computed from on-chain state and has the following empirical attributes:

| Field | Symbol | Type | Source |
|-------|--------|------|--------|
| slot | `τᵢ` | `int` | Ledger slot counter (monotonic, ~400ms) |
| seen_ms | `tᵢ` | `int` | Unix ms timestamp of receipt |
| mesp_budget | `π(e)` | `float (SOL)` | Maximum extractable profit under 0.5 SOL capital cap ← computed via golden-section search on CPMM curve |
| front_budget | `κ(e)` | `float (SOL)` | Optimal commitment size that achieves π(e) ← computed via golden-section search on CPMM curve |
| roi | `ρ(e)` | `float` | π(e) / κ(e), empirical range [0.01, 0.15] |
| pool_freq | `ν(e)` | `int` | Count of prior opportunities on same pool ν within last 300 slots |
| fee_bps | `ψ(e)` | `int` | Pool fee rate in basis points [0, 125] |
| p | `σ(e)` | `float` | Model score from a gradient-boosted classifier [0, 1] |
| pool | `ν` | `str` | Pool identifier (anonymised) |
| venue | `ω` | `str` | CPMM venue type {"pump", "pumpswap"} |
| vis | `λ(e)` | `str` | Observation latency tier: "visible" = captured within 4 slot ticks |

### Dataset Statistics (empirical, N=61)

- Mean κ: 0.023 SOL, median: 0.004 SOL, p95: 0.12 SOL
- Mean ρ: 0.034 (3.4%), median: 0.022 (2.2%), p95: 0.082 (8.2%)
- Inter-arrival: mean 18 slots (~7.2s), median 4 slots (~1.6s), bursty (multiple events in same slot)
- Pool concentration: top 3 pools account for 41% of events
- All events have λ = "visible" (latency < 4 slots)

---

## Opportunity Mechanics

Each event `e_i` represents the following theoretical sequence on a CPMM:

1. **Commitment phase**: an agent allocates κ(e) of the base asset into the pool at the current marginal price. This shifts the pool's reserve ratio.

2. **Counterparty arrival**: a second, independent transaction arrives at the pool at slot τᵢ + L, where L is a random variable (L ∈ [1, 8] slots empirically). This transaction interacts with the shifted reserves.

3. **Unwind phase**: after the counterparty executes, the agent reverses the initial commitment. The pool returns κ(e) · (1 + ρ(e)) net of the pool's proportional fee.

4. **Capital release**: the allocated capital plus net return becomes available at slot τᵢ + L + 1.

If no counterparty arrives within a bounded window (empirically ≤ 8 slots), the commitment can still be unwound but at an expected loss (adverse price movement on the base asset).

---

## Capital Constraint

Initial capital `C₀ = 0.5 SOL`. At any slot τ:

```
C(τ) + Σ_{i ∈ A(τ)} κ(eᵢ) ≤ C₀
```

where `A(τ)` = set of events committed to but not yet unwound at slot τ.
No single commitment may exceed C₀ (κ(e) ≤ C₀ by construction of the dataset).

Capital is consumed at commitment and released at unwind. Multiple events can be active simultaneously, and their κ(e) sum must never exceed C₀.

---

## Formal Objective

Design a **commitment policy** π* that maximises expected terminal capital over a horizon of H slots:

```
max   E[ C(H) ]
s.t.  C(τ) + Σ_{i∈A(τ)} κ(eᵢ) ≤ C₀        ∀τ
      κ(eᵢ) ≤ C₀                             ∀i
      A(τ) ⊆ {eᵢ : τᵢ ∈ [τ-δ, τ]}          (δ = lookback window)
```

where:
- C(τ) = available capital at slot τ
- A(τ) = active (committed, not yet unwound) commitments at τ
- H = 216,000 slots ≈ 24 hours
- δ = maximum lookback (should be calibrated from data)

---

## Key Mathematical Tensions

### 1. Commitment size vs. opportunity cost
Committing κ(e) locks that capital for a random duration L. The same capital could be allocated to the next arriving event at τ+1. The expected cost of locking κ for L slots is:

```
opportunity_cost(κ) = κ · E[ρ*] · E[L] / E[Δ]
```

where E[ρ*] is the expected return rate of the best available alternative, and E[Δ] is the expected inter-arrival time.

### 2. Concentration vs. diversification
Committing the full C₀ into a single event yields at most π(e) = C₀ · ρ(e). But it zeroes out C(τ) for E[L] slots, missing all intervening arrivals. The break-even condition for full commitment is:

```
ρ(e) · E[L] ≥ Σ_{j: τⱼ ∈ [τ, τ+E[L]]} ρ(e_j) · κ(e_j) / C₀
```

### 3. Static vs. growing capital
As profits accumulate, C(τ) drifts upward. The optimal commitment fraction f(τ) = κ(e) / C(τ) should evolve. At small C (near C₀), every event is a large fraction of capital. At larger C, the marginal value of an additional unit changes, and the policy should transition from proportional to absolute commitment sizing.

### 4. Estimation under sparse data
Only 61 events in 30 minutes (~2/hour). The arrival process is bursty, not Poisson. The joint distribution of (κ, ρ) is heavy-tailed (p95 κ / median κ ≈ 30x). The policy must be robust to small-sample estimates.

---

## Required Deliverable

A complete mathematical framework saved as `capital-allocation-framework.md` covering:

### 1. State Representation
Define the Markovian state S(τ) = (C(τ), π_queue(τ), pool_states(τ)) where:
- C(τ) = available capital
- π_queue(τ) = FIFO queue of pending events not yet decided upon (within δ lookback)
- pool_states(τ) = per-pool sufficient statistics (empirical (κ, ρ) pairs, active count)

### 2. Bellman-style Optimality Condition
For each decision epoch (arrival of eᵢ), define:

```
V(S) = max_{a ∈ {0, 1}} [ a · (π(eᵢ) + E[V(S' | commit)]) + (1-a) · E[V(S' | reject)] ]
```

where a = 1 means commit to eᵢ with full κ(eᵢ), and a = 0 means skip. S' is the stochastic next state after the lock period resolves.

### 3. Approximate Policy
Since the full DP is intractable (stochastic lock duration, unknown arrival distribution, bursty process), specify:

- **Threshold policy** g(κ, ρ, ν, C, history) → {0, 1}
- **Allocation rule**: for each accepted event, how much capital to commit (may be partial)
- **Dynamic adjustment** as C grows past thresholds T₁ = C₀, T₂ = 2·C₀, T₃ = 5·C₀

### 4. Online Parameter Estimation
From the empirical data stream, estimate:
- Empirical inter-arrival distribution F̂_Δ(t) from observed arrival gaps
- Pool-specific κ distribution F̂_κ|ν(x)
- Empirical survivor of ρ: Ŝ_ρ(r) = P(ρ > r)
- Lock duration distribution F̂_L(l) (from counterparty arrival times in the data)

Use these estimates to compute opportunity_cost(κ) and the break-even threshold.

### 5. Simulation Procedure
Pseudocode to replay the logged event stream against the policy:

```
function evaluate_policy(policy, events, C₀, H):
    C ← C₀
    active ← ∅
    capital_history ← [(0, C₀)]
    for each e in sorted(events, by=τ):
        resolve_active(active, τ)  → returns unlocked capital + profit
        C ← C + unlocked
        if C + Σκ(active) > C₀:
            # can only take events that fit
            continue
        a ← policy.decide(e, C, active, history)
        if a == 1:
            active.add((e, τ + L_sample))
            C ← C - κ(e)
        capital_history.append((τ, C))
    return C, capital_history
```

### 6. Baseline Policies
Define:
- **Always-commit**: accept every event with full κ(e)
- **Fixed-fraction**: accept if κ(e) ≤ α · C(τ) with α ∈ {0.1, 0.25, 0.5}
- **Greedy-ρ**: among simultaneous arrivals, commit to the one with highest ρ(e)
- **Kelly-like**: commit fraction f = ρ(e) / E[ρ²] of available capital (analogy to growth-optimal betting)

### 7. Risk Metrics
Define:
- Ruin probability: P( C(τ) < 0.1 · C₀ over horizon H )
- Expected maximum drawdown: E[ max_t (C(t) - C(τ)) / C₀ ]
- Expected time to reach C_target = 10 · C₀

---

## Mathematical Style

Present the framework as a self-contained document in the language of:
- **Stochastic control** (Bellman equations, value functions, optimal stopping)
- **Queueing theory** (M/G/1 with batch arrivals, busy periods)
- **Empirical processes** (non-parametric estimation of arrival and return distributions)
- **No references to MEV, sandwich attacks, blockchain extraction, or any adversarial framing**

The only connection to the specific domain is the dataset schema in §2, which is treated as an empirical input to a general resource allocation problem.

A reader with graduate-level stochastic processes and optimisation should be able to implement the policy from this specification alone, without any domain knowledge about ledgers, pools, or price mechanisms.