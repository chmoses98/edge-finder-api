# MLB-RSCH-0029 -- Hitter Declared-Edge Decomposition

**RESEARCH ONLY. No production change. Parameters fitted: 0.**

## The algebra, and a correction

An earlier session guessed that wide bid/ask spreads mechanically produce large declared
edges. **That is backwards.** Decomposing about the vig-free midpoint:

```
declaredEdge = (model - fairMid) - (executablePrice - fairMid)
             =  MODEL_SIGNAL     -  EXECUTION_PENALTY
```

A *larger* execution penalty **reduces** declared edge. So the mechanism had to be measured,
not assumed.

## What production actually computes

- Source: `lib/research/hitter_pricing.py::price_hitter_contract`
- Formula: `rawProbabilityEdge = modelProbability - executableKalshiPrice`
- Executable price: lib/research/hitter_board_builder.py::_executable_yes_price -- mid if present, else (yes_bid+yes_ask)/2, else ask, else bid
- Side semantics: YES only; no NO expression exists in this path
- Fee-aware: **False** · Spread adjustment: none -- production differences against the MIDPOINT
- Staleness term: none -- marketObservedAt is recorded but never used to adjust
- Reproducible from archived source fields: **True**

### The decomposition collapses

- `executionPenalty == 0` on **100.00%** of rows (max |penalty| 0.00e+00)
- `declaredEdge` reproduced from source fields on **100.00%** of rows

**production's executable price IS the vig-free midpoint, so EXECUTION_PENALTY is identically zero and declaredEdge == MODEL_SIGNAL exactly.**

Execution cost therefore *cannot* be the cause of the inversion: it is not present in the
quantity that inverts.

## H1 -- model signal (the decisive test)

Paired delta is model minus Kalshi fair midpoint; negative means the model is better.

| Model signal | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| [-1.000,+0.000) | 2719 | 244 | +0.00314 | PARITY |
| [+0.000,+0.025) | 762 | 213 | +0.00071 | PARITY |
| [+0.025,+0.050) | 656 | 193 | +0.00019 | PARITY |
| [+0.050,+0.075) | 383 | 148 | +0.00446 | PARITY |
| [+0.075,+0.100) | 263 | 105 | +0.00889 | PARITY |
| [+0.100,+0.150) | 319 | 94 | +0.01169 | PARITY |
| [+0.150,+1.010) | 66 | 27 | -0.02255 | INSUFFICIENT_SAMPLE |

- Monotone improving: **False**
- **Inversion: True**

## H2 -- execution penalty and spread

Production prices against the midpoint, so its declared edge omits the half-spread a taker actually pays. That makes its stated edge optimistic; it does not make the edge inversion an execution artifact.

True taker penalty (ask - mid), which production's edge omits: `{'n': 5168, 'min': 0.005, 'p25': 0.005, 'median': 0.005, 'p75': 0.01, 'max': 0.265, 'mean': 0.0088}`

| Spread (cents) | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| [0.0,1.0) | 0 | - | - | INSUFFICIENT_SAMPLE |
| [1.0,2.0) | 3108 | 257 | +0.00232 | PARITY |
| [2.0,4.0) | 1778 | 247 | +0.00349 | PARITY |
| [4.0,8.0) | 249 | 99 | +0.00742 | PARITY |
| [8.0,100.0) | 33 | 15 | +0.00705 | INSUFFICIENT_SAMPLE |

## H3 -- quote age

- Rows with measurable age: 5168 · without timestamps: 0 (never fabricated)
- Age (minutes): `{'n': 5168, 'min': 0.0, 'p25': 0.0, 'median': 0.0, 'p75': 0.0, 'max': 294.645, 'mean': 15.9524}`

| Age (min) | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| [0,15) | 4294 | 259 | +0.00373 | MARKET_BEATS_MODEL |
| [15,60) | 466 | 79 | +0.00073 | PARITY |
| [60,180) | 309 | 49 | -0.00011 | PARITY |
| [180,1000000000) | 99 | 26 | -0.00834 | INSUFFICIENT_SAMPLE |

## H5 -- probability extremeness

| Probability band | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| [0.00,0.10) | 1623 | 256 | +0.00181 | PARITY |
| [0.10,0.25) | 1633 | 259 | +0.00365 | PARITY |
| [0.25,0.50) | 1132 | 250 | +0.00128 | PARITY |
| [0.50,0.75) | 699 | 245 | +0.00676 | PARITY |
| [0.75,1.00) | 81 | 56 | +0.00536 | INSUFFICIENT_SAMPLE |

Signal inversion *within* each probability band:

- `[0.00,0.10)`: rows=1623 inversion=False qualifyingBuckets=3
- `[0.10,0.25)`: rows=1633 inversion=False qualifyingBuckets=3
- `[0.25,0.50)`: rows=1132 inversion=False qualifyingBuckets=6
- `[0.50,0.75)`: rows=699 inversion=True qualifyingBuckets=4
- `[0.75,1.00)`: rows=81 inversion=None qualifyingBuckets=-

## H6 -- by family

| Family | Rows | Keys | Paired delta | p | FDR | Signal inversion | Verdict |
|---|---:|---:|---:|---:|:-:|:-:|---|
| hitter_hits | 1172 | 254 | +0.00293 | 0.3716 | no | True | PARITY |
| hitter_total_bases | 1423 | 252 | +0.00204 | 0.4065 | no | False | PARITY |
| hitter_hits_runs_rbis | 1795 | 256 | +0.00251 | 0.3616 | no | False | PARITY |
| hitter_rbis | 778 | 252 | +0.00600 | 0.1322 | no | None | PARITY |

## H7 -- ladder depth / tail structure

Ladders found: 953. Ladder rungs share a player-game and are never counted as independent.

| Rung | Rows | Keys | Mean model signal | Mean model prob | Paired delta | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 0 | 953 | 255 | 0.014142 | 0.4861 | +0.00186 | PARITY |
| 1 | 953 | 255 | 0.00581 | 0.3193 | +0.00229 | PARITY |
| 2 | 789 | 242 | -0.002818 | 0.2206 | +0.00261 | PARITY |
| 3 | 604 | 225 | -0.004407 | 0.198 | +0.00281 | PARITY |

## Chronological

- DEVELOPMENT (<= 2026-08-22): 4326 rows / 208 keys, signal inversion **True**
- VALIDATION: 842 rows / 53 keys, signal inversion **False**

## Mechanism

**CASE_A_MODEL_SIGNAL_INVERSION**

model-minus-fair-market disagreement itself degrades as it grows, and the execution penalty is identically zero in production's declared edge, so execution cost cannot be the cause.

## Secondary economics

Reported at BOTH conventions. The gap between them is precisely the half-spread production's
declared edge omits.

| Segment | Entry | Opportunities | Wins | Avg entry | Fees | Net | ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| OVERALL_at_honest_ask | ask | 2169 | 659 | 0.3078 | 35.36 | -44.08 | -0.066 |
| OVERALL_at_production_mid | mid | 2445 | 725 | 0.2894 | 39.14 | -21.72 | -0.0307 |
| signal_[-1.000,+0.000)_at_ask | ask | 0 | 0 | None | 0.0 | 0.0 | None |
| signal_[+0.000,+0.025)_at_ask | ask | 487 | 88 | 0.2063 | 6.9 | -19.39 | -0.193 |
| signal_[+0.025,+0.050)_at_ask | ask | 652 | 166 | 0.2506 | 9.83 | -7.2 | -0.0441 |
| signal_[+0.050,+0.075)_at_ask | ask | 382 | 136 | 0.3748 | 6.75 | -13.91 | -0.0972 |
| signal_[+0.075,+0.100)_at_ask | ask | 263 | 105 | 0.4069 | 4.81 | -6.82 | -0.0637 |
| signal_[+0.100,+0.150)_at_ask | ask | 319 | 123 | 0.382 | 5.79 | -4.64 | -0.0381 |

## Filter simulations

**Not simulated.** mechanism is CASE_A_MODEL_SIGNAL_INVERSION; a spread/staleness filter has no mechanism to act on, so simulating one would be post-hoc fishing.

## Result

- Mechanism: **CASE_A_MODEL_SIGNAL_INVERSION**
- Disposition: **LEVEL_0_MEASUREMENT_ONLY** (maximum permitted: SHADOW_CANDIDATE)
- Shadow candidate justified: **False**
- Production activation authorized: False
