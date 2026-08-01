# EdgeLab Phase 2 Milestone 5 — Market Comparison Report

_Generated 2026-08-01T16:37:05Z_

**RESEARCH ONLY.** This report compares different ways of expressing the
same underlying baseball edge (e.g. full-game ML vs F5 ML vs run line).
It does not change, and is not consulted by, any production recommendation,
staking, or bet-selection code path. See docs/EDGELAB_MARKET_COMPARISON.md.

Total markets compared: **255**

## Comparison status counts

| Status | Count |
|---|---|
| BEST_EXPRESSION | 4 |
| ALTERNATIVE_EXPRESSION | 0 |
| DOMINATED_MARKET | 3 |
| INCOMPLETE_COMPARISON | 227 |
| NO_MODEL_SUPPORT | 0 |
| LOW_DATA_QUALITY | 6 |
| LOW_LIQUIDITY | 0 |
| HIGH_TIE_RISK | 0 |
| DISTINCT_THESIS | 15 |
| NOT_COMPARABLE | 0 |

## Historical analysis

- Games with comparable markets: **4**
- Expression clusters (size > 1): **4**
- Placed-bet audit sample size: **0** (INSUFFICIENT_SAMPLE)
- Placed bets that were NOT the top-ranked expression in their cluster: **0**

### Best-expression counts by canonical family
- game_result: 4

### Dominated-market counts by canonical family
- game_result: 3

### Missing-data blockers (INCOMPLETE_COMPARISON, by missing field set)
- `confidence,dataQuality,estimatedEdge,marketImpliedProbability,modelFairProbability`: 119
- `confidence,estimatedEdge,marketImpliedProbability,modelFairProbability`: 66
- `confidence`: 34
- `(none)`: 8

### Dominated-market examples
| Market | Dominated by | Reasons |
|---|---|---|
| KXMLBGAME-26JUL312040KCCOL-KC | KXMLBF5-26JUL312040KCCOL-KC | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26JUL302140BOSATH-BOS | KXMLBF5-26JUL302140BOSATH-BOS | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26JUL301910MIANYM-NYM | KXMLBF5-26JUL301910MIANYM-NYM | HIGHER_EV, LOWER_MATERIAL_RISK |

### Best-expression examples
| Market | Cluster | Score |
|---|---|---|
| KXMLBGAME-26JUL312015TEXHOU-TEX | 824164:AWAY:WIN | 0.573 |
| KXMLBGAME-26JUL311910AZCLE-AZ | 824407:AWAY:WIN | 0.579 |
| KXMLBGAME-26JUL311907STLTOR-STL | 822782:AWAY:WIN | 0.586 |
| KXMLBGAME-26JUL311810PITCIN-PIT | 824486:AWAY:WIN | 0.639 |

## Known limitations

- `liquidity` is never populated -- no volume/depth field exists anywhere in this schema.
- `bidAskSpread` is only known for markets with a placed bet's CLV quote, so most
  evaluated-but-never-bet markets show LOW_LIQUIDITY/bidAskSpread as unknown, not zero.
- The comparison score's weights (SCORE_WEIGHTS) are illustrative defaults, not tuned
  or backtested against outcome data.
- Pitcher strikeouts/outs markets are not in the current 11-market production set
  (config/rules.json), so PLAYER_PROP clustering/domination is structurally supported
  but currently exercised only by this module's tests, not by real historical data.
- This report is research-only: it does not change production recommendations,
  staking, or bet selection.
