# EdgeLab Phase 2 Milestone 5 — Market Comparison Report

_Generated 2026-08-20T19:41:07Z_

**RESEARCH ONLY.** This report compares different ways of expressing the
same underlying baseball edge (e.g. full-game ML vs F5 ML vs run line).
It does not change, and is not consulted by, any production recommendation,
staking, or bet-selection code path. See docs/EDGELAB_MARKET_COMPARISON.md.

Total markets compared: **58495**

## Comparison status counts

| Status | Count |
|---|---|
| BEST_EXPRESSION | 62 |
| ALTERNATIVE_EXPRESSION | 1 |
| DOMINATED_MARKET | 6 |
| INCOMPLETE_COMPARISON | 58230 |
| NO_MODEL_SUPPORT | 0 |
| LOW_DATA_QUALITY | 53 |
| LOW_LIQUIDITY | 1 |
| HIGH_TIE_RISK | 8 |
| DISTINCT_THESIS | 134 |
| NOT_COMPARABLE | 0 |

## Historical analysis

- Games with comparable markets: **309**
- Expression clusters (size > 1): **499**
- Placed-bet audit sample size: **7** (INSUFFICIENT_SAMPLE)
- Placed bets that were NOT the top-ranked expression in their cluster: **2**

### Best-expression counts by canonical family
- game_result: 37
- inning_result: 14
- team_total: 11

### Dominated-market counts by canonical family
- game_result: 6

### Missing-data blockers (INCOMPLETE_COMPARISON, by missing field set)
- `confidence,dataQuality,estimatedEdge,marketImpliedProbability,modelFairProbability`: 57214
- `confidence`: 572
- `confidence,estimatedEdge,marketImpliedProbability,modelFairProbability`: 272
- `confidence,dataQuality`: 148
- `(none)`: 23
- `estimatedEdge,marketImpliedProbability,modelFairProbability`: 1

### Dominated-market examples
| Market | Dominated by | Reasons |
|---|---|---|
| KXMLBGAME-26JUL302140BOSATH-BOS | KXMLBF5-26JUL302140BOSATH-BOS | HIGHER_EV, INFERIOR_NET_EV, LOWER_MATERIAL_RISK, STARTER_ONLY_THESIS_PREFERS_F5, DUPLICATE_THESIS |
| KXMLBGAME-26JUL301910MIANYM-NYM | KXMLBF5-26JUL301910MIANYM-NYM | HIGHER_EV, INFERIOR_NET_EV, LOWER_MATERIAL_RISK, STARTER_ONLY_THESIS_PREFERS_F5, DUPLICATE_THESIS |
| KXMLBGAME-26AUG051940MINKC-KC | KXMLBF5-26AUG051940MINKC-KC | HIGHER_EV, INFERIOR_NET_EV, LOWER_MATERIAL_RISK, STARTER_ONLY_THESIS_PREFERS_F5, DUPLICATE_THESIS |
| KXMLBGAME-26AUG051840NYMCLE-NYM | KXMLBF5-26AUG051840NYMCLE-NYM | HIGHER_EV, INFERIOR_NET_EV, LOWER_MATERIAL_RISK, STARTER_ONLY_THESIS_PREFERS_F5, DUPLICATE_THESIS |
| KXMLBGAME-26JUL312040KCCOL-KC | KXMLBF5-26JUL312040KCCOL-KC | HIGHER_EV, INFERIOR_NET_EV, LOWER_MATERIAL_RISK, STARTER_ONLY_THESIS_PREFERS_F5, DUPLICATE_THESIS |
| KXMLBGAME-26AUG102210KCLAD-KC | KXMLBF5-26AUG102210KCLAD-KC | HIGHER_EV, INFERIOR_NET_EV, DUPLICATE_THESIS |

### Best-expression examples
| Market | Cluster | Score |
|---|---|---|
| KXMLBGAME-26AUG161340WSHNYM-NYM | 823590:HOME:WIN | 0.548 |
| KXMLBF5-26AUG161607KCLAA-KC | 823991:AWAY:WIN | 0.645 |
| KXMLBF5-26AUG161410PHIMIN-PHI | 823670:AWAY:WIN | 0.619 |
| KXMLBGAME-26AUG072140HOUSD-SD | 823266:HOME:WIN | 0.540 |
| KXMLBF5-26AUG161335BOSPIT-BOS | 823344:AWAY:WIN | 0.657 |
| KXMLBGAME-26AUG072140LADAZ-AZ | 825051:HOME:WIN | 0.546 |
| KXMLBGAME-26AUG071940CLECWS-CLE | 824566:AWAY:WIN | 0.599 |
| KXMLBGAME-26AUG071905ATLNYY-NYY | 823515:HOME:WIN | 0.539 |
| KXMLBGAME-26AUG071845CINWSH-WSH | 822699:HOME:WIN | 0.591 |
| KXMLBGAME-26AUG071840NYMPIT-NYM | 823349:AWAY:WIN | 0.600 |

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
