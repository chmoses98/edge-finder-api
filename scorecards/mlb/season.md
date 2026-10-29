## MLB projection scorecard (season) — season to date

Generated 2026-10-29T23:21:51Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1372 across 7 games. Settled: 1157 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 13 (7) | 3.193 | +2.012 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 14 (7) | 2.454 | +0.470 | 1.861 | 86% | — |
| game_total | incumbent_team_runs (incumbent) | 6 (6) | 2.203 | -0.150 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 7 (7) | 2.463 | +0.794 | 1.557 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 39 (3) | 0.702 | +0.176 | — | — |  |
| hits | pf-mlb:F (challenger) | 126 (7) | 0.709 | -0.019 | 0.456 | 81% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 39 (3) | 1.279 | +0.070 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 126 (7) | 1.351 | +0.074 | 0.858 | 84% | — |
| home_runs | pf-mlb:F (challenger) | 126 (7) | 0.146 | +0.060 | 0.050 | 80% | — |
| outs | incumbent_pitcher_champion (incumbent) | 13 (7) | 3.163 | +1.551 | — | — |  |
| outs | pf-mlb:F (challenger) | 14 (7) | 2.918 | +0.949 | 2.097 | 79% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 13 (7) | 1.530 | +1.017 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 14 (7) | 1.492 | +1.061 | 0.958 | 86% | — |
| plate_appearances | pf-mlb:F (challenger) | 126 (7) | 0.573 | -0.062 | 0.363 | 70% | — |
| rbis | incumbent_hitter_engine (incumbent) | 38 (3) | 0.505 | -0.052 | — | — |  |
| rbis | pf-mlb:F (challenger) | 126 (7) | 0.533 | +0.031 | 0.282 | 83% | — |
| runs | pf-mlb:F (challenger) | 126 (7) | 0.488 | +0.062 | 0.244 | 80% | — |
| team_runs | incumbent_team_runs (incumbent) | 12 (6) | 1.812 | -0.075 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 14 (7) | 1.770 | +0.397 | 1.239 | 93% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 39 (3) | 1.060 | +0.285 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 126 (7) | 1.110 | +0.136 | 0.700 | 85% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 14 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 7 settled forecasts (< 100)
- hits: **OK** — within expectations
- hits_runs_rbis: **OK** — within expectations
- home_runs: **ALERT** — bias +0.060 (95% CI +0.027..+0.105) exceeds 5% of the mean
- outs: **INSUFFICIENT** — 14 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 14 settled forecasts (< 100)
- plate_appearances: **OK** — within expectations
- rbis: **OK** — within expectations
- runs: **OK** — within expectations
- team_runs: **INSUFFICIENT** — 14 settled forecasts (< 100)
- total_bases: **ALERT** — bias +0.136 (95% CI +0.012..+0.271) exceeds 5% of the mean

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 13 | 2.6273 | 0.4912 | 12 | 3.1169 |
| short_rest(<=4d) | 4 | 2.6144 | -1.058 | 3 | 4.5607 |
| normal_rest(>4d) | 10 | 2.3895 | 1.0815 | 10 | 2.7827 |
| early_exit(actual BF<=12) | 2 | 0.73 | -0.5323 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 12 | 2.741 | 0.6373 | 12 | 3.1169 |
| starter_CONFIRMED | 14 | 2.4538 | 0.4702 | 13 | 3.193 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 13 | 2.9863 | 0.866 | 12 | 3.1917 |
| short_rest(<=4d) | 4 | 1.755 | 0.5888 | 3 | 2.636 |
| normal_rest(>4d) | 10 | 3.3838 | 1.0937 | 10 | 3.3206 |
| early_exit(actual BF<=12) | 2 | 1.1638 | 1.1638 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 12 | 3.2108 | 0.9137 | 12 | 3.1917 |
| starter_CONFIRMED | 14 | 2.9184 | 0.9495 | 13 | 3.1626 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 13 | 1.4611 | 0.9972 | 12 | 1.4945 |
| short_rest(<=4d) | 4 | 1.1794 | 1.1794 | 3 | 1.6051 |
| normal_rest(>4d) | 10 | 1.6166 | 1.0136 | 10 | 1.5079 |
| early_exit(actual BF<=12) | 2 | 0.963 | 0.963 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 12 | 1.5798 | 1.0773 | 12 | 1.4945 |
| starter_CONFIRMED | 14 | 1.4917 | 1.061 | 13 | 1.5304 |

**Participation:** pitcher forecasts resolved 16; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
