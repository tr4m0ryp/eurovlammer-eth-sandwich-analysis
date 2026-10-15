# Optimal Capital Allocation Under Stochastic Opportunity Arrival

## Problem Statement

We observe a stream of events from a live Solana block processor. Each event represents an **arbitrage opportunity** on a constant-product AMM (automated market maker). The stream is ordered by slot (~400ms slot time) and each event carries a set of real-valued attributes.

### Event Schema

Each arriving opportunity `e_i` contains:

| Field | Type | Description |
|-------|------|-------------|
| `τ` | `int` | Solana slot at which the opportunity was observed (monotonic, ~400ms spacing) |
| `t` | `int` | Unix ms timestamp of receipt |
| `π(e)` | `float (SOL)` | Maximum extractable profit given a binding capital constraint of 0.5 SOL |
| `κ(e)` | `float (SOL)` | Optimal commitment size — the amount of capital that must be locked to realise π(e) |
| `ρ(e)` | `float` | Return rate = π(e) / κ(e), in [0.01, 0.15] |
| `ν(e)` | `int` | Pool identifier | 
| `φ(e)` | `int` | Frequency count: how many opportunities on pool ν(e) in the last 300 slots |
| `ψ(e)` | `float` | Fee in basis points (30–125) |
| `w(e)` | `str` | "visible" — indicates the opportunity was captured with < 4 slots of latency |

### Opportunity Mechanics

Each opportunity represents a theoretical price-reversal arbitrage on a CPMM curve:

1. **Commitment phase**: κ(e) SOL is locked into the AMM pool (purchasing pool tokens)
2. **Lock period**: a subsequent transaction (the "counterparty") is expected to arrive naturally within 1–6 slots (0.4–2.4 seconds). The counterparty interacts with the same pool state.
3. **Unwind phase**: after the counterparty executes, the initial commitment is reversed. The pool returns κ(e) × (1 + ρ(e)) SOL minus pool fees.
4. **Capital release**: the full κ(e) + π(e) SOL is released after unwind.

The lock period is stochastic: the counterparty's arrival slot is unknown at commitment time. If no counterparty arrives within a bounded window (~8 slots), the commitment can still be unwound but at a stochastic loss (adverse price movement).

### Capital Constraint

We maintain a single capital pool of size `C(0) = C_0 = 0.5 SOL`. At any slot `τ`:

```
C(τ) + Σ_{i in active} κ(e_i) ≤ C_0
```

where `active` is the set of opportunities committed to but not yet unwound at slot `τ`. Total locked capital cannot exceed initial capital.

### Opportunity Arrival Process

Opportunities arrive as a marked point process on the slot timeline. Multiple opportunities can arrive in the same slot (parallel). The inter-arrival distribution and the joint distribution of (κ, ρ, ν) are unknown and must be estimated online.

---

## Formal Objective

Design a **commitment policy** π* that maximises terminal wealth over a horizon of H slots (H ≈ 216,000 ≈ 24 hours):

```
max   E[ C(H) ]
s.t.  ∀τ: C(τ) + Σ_{i∈A(τ)} κ(e_i) ≤ C_0
      A(τ) ⊆ {e_i : τ_i ∈ [τ-δ, τ]}  (only recent opportunities considered)
      κ(e_i) ≤ C_0                     (single commitment bounded by initial capital)
```

where:
- `C(τ)` is available (unlocked) capital at slot τ
- `A(τ)` is the set of active (committed, not yet unwound) opportunities at τ
- `δ` is the maximum lookback window for considering an opportunity

## Key Tensions

1. **Scale vs. opportunity cost**: committing κ(e) locks that capital for L slots. The same capital could have been used for the next opportunity arriving 1 slot later if κ(e) is small, or for a larger opportunity with higher ρ if kept uncommitted.

2. **Concentration vs. diversification**: committing the full C_0 into a single high-ρ opportunity yields maximum per-event return but leaves zero capacity for all subsequent opportunities during the lock period.

3. **Static vs. growing capital**: as profits accumulate, C(τ) grows. The optimal commitment fraction f(τ) = κ(e) / C(τ) should evolve with C(τ). At small C, every opportunity is a large fraction of capital. At larger C, the marginal value of an additional unit of capital changes.

4. **Estimation under sparse data**: the first few opportunities provide minimal information about the arrival process and the joint distribution of (κ, ρ). The policy must balance exploration (committing to learn) vs. exploitation.

---

## Required Deliverable

A complete mathematical framework in markdown that specifies:

### 1. State Representation
Define the Markovian state `S(τ)` that captures all information needed for decision-making at slot τ. This must include both the remaining capital and sufficient statistics of the observed opportunity process.

### 2. Bellman-style Optimality Condition
Write the optimality equation for the value function V(S(τ)), treating each arriving opportunity as a decision node. The action space is {accept with commitment size a ∈ [0, min(κ(e), C(τ))], reject}.

### 3. Approximate Solution
Since the full DP is intractable (infinite horizon, unknown arrival distribution), propose an approximate policy. This must include:

- A **threshold function** g(κ, ρ, ν, C, history) → {accept, reject}
- An **allocation rule** a(κ, C) → commitment amount
- A **dynamic adjustment** as C grows beyond some threshold T

### 4. Online Parameter Estimation
Specify how to estimate from the observed stream:
- Arrival rate λ(τ) = E[#opportunities per slot]
- Conditional distribution of κ | ν (pool-specific)
- Empirical survivor function of ρ
- Lock period distribution (from counterparty arrival times)

### 5. Simulation Procedure
Write pseudocode for replaying a logged event stream against the policy to measure empirical performance. This is a Monte Carlo evaluation, not live execution.

### 6. Comparative Baselines
Define at least three baseline policies for comparison:
- **Always-commit**: take every opportunity with full κ(e)
- **Fixed-fraction**: commit κ_fixed = α · C(τ) for each accepted opportunity
- **Greedy top-ρ**: among opportunities in the current slot, commit to the one with highest ρ

### 7. Risk Metrics
Define: probability of ruin (C(τ) < 0.1 · C_0), expected drawdown, and expected time to reach a target C_target = 10 · C_0.

---

## Mathematical Style

Present the framework using:
- Random variables in upper case
- Realised values in lower case
- Stochastic processes indexed by slot τ
- Conditional expectations where arrival distributions are unknown
- No references to trading, bots, execution, or real financial systems
- Pure operations-research / stochastic-control framing

The final document should be self-contained: a reader with graduate-level stochastic processes and optimisation should be able to implement the policy from the specification alone.

Save the final result to a file named `capital-allocation-framework.md` in the working directory.