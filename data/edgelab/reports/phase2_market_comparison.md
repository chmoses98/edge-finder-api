# EdgeLab Phase 2 Milestone 5 — Market Comparison Report

_Generated 2026-08-15T15:47:59Z_

**RESEARCH ONLY.** This report compares different ways of expressing the
same underlying baseball edge (e.g. full-game ML vs F5 ML vs run line).
It does not change, and is not consulted by, any production recommendation,
staking, or bet-selection code path. See docs/EDGELAB_MARKET_COMPARISON.md.

Total markets compared: **47803**

## Comparison status counts

| Status | Count |
|---|---|
| BEST_EXPRESSION | 43 |
| ALTERNATIVE_EXPRESSION | 1 |
| DOMINATED_MARKET | 6 |
| INCOMPLETE_COMPARISON | 47608 |
| NO_MODEL_SUPPORT | 0 |
| LOW_DATA_QUALITY | 49 |
| LOW_LIQUIDITY | 1 |
| HIGH_TIE_RISK | 6 |
| DISTINCT_THESIS | 89 |
| NOT_COMPARABLE | 0 |

## Historical analysis

- Games with comparable markets: **220**
- Expression clusters (size > 1): **300**
- Placed-bet audit sample size: **7** (INSUFFICIENT_SAMPLE)
- Placed bets that were NOT the top-ranked expression in their cluster: **2**

### Best-expression counts by canonical family
- game_result: 30
- inning_result: 6
- team_total: 7

### Dominated-market counts by canonical family
- game_result: 6

### Missing-data blockers (INCOMPLETE_COMPARISON, by missing field set)
- `confidence,dataQuality,estimatedEdge,marketImpliedProbability,modelFairProbability`: 46941
- `confidence`: 318
- `confidence,estimatedEdge,marketImpliedProbability,modelFairProbability`: 266
- `confidence,dataQuality`: 59
- `(none)`: 23
- `estimatedEdge,marketImpliedProbability,modelFairProbability`: 1

### Dominated-market examples
| Market | Dominated by | Reasons |
|---|---|---|
| KXMLBGAME-26AUG051940MINKC-KC | KXMLBF5-26AUG051940MINKC-KC | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26AUG051840NYMCLE-NYM | KXMLBF5-26AUG051840NYMCLE-NYM | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26JUL312040KCCOL-KC | KXMLBF5-26JUL312040KCCOL-KC | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26JUL302140BOSATH-BOS | KXMLBF5-26JUL302140BOSATH-BOS | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26JUL301910MIANYM-NYM | KXMLBF5-26JUL301910MIANYM-NYM | HIGHER_EV, LOWER_MATERIAL_RISK |
| KXMLBGAME-26AUG102210KCLAD-KC | KXMLBF5-26AUG102210KCLAD-KC | HIGHER_EV |

### Best-expression examples
| Market | Cluster | Score |
|---|---|---|
| KXMLBGAME-26AUG082010LADAZ-AZ | 825049:HOME:WIN | 0.603 |
| KXMLBGAME-26AUG081915CLECWS-CLE | 824565:AWAY:WIN | 0.540 |
| KXMLBF5-26AUG081910MINMIL-MIN | 823752:AWAY:WIN | 0.611 |
| KXMLBGAME-26AUG081915HOUSD-HOU | 823267:AWAY:WIN | 0.538 |
| KXMLBF5-26AUG081805TORPHI-TOR | 823426:AWAY:WIN | 0.606 |
| KXMLBGAME-26AUG081845CINWSH-WSH | 822701:HOME:WIN | 0.592 |
| KXMLBGAME-26AUG051835LAABAL-LAA | 824806:AWAY:WIN | 0.598 |
| KXMLBGAME-26JUL312015TEXHOU-TEX | 824164:AWAY:WIN | 0.573 |
| KXMLBGAME-26JUL311910AZCLE-AZ | 824407:AWAY:WIN | 0.579 |
| KXMLBGAME-26JUL311907STLTOR-STL | 822782:AWAY:WIN | 0.586 |

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
