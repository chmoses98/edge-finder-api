## MLB projection scorecard (season) — season to date

Generated 2026-10-26T20:52:17Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1004 across 5 games. Settled: 731 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 8 (4) | 3.494 | +2.267 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 8 (4) | 3.254 | +0.988 | 2.414 | 75% | — |
| game_total | incumbent_team_runs (incumbent) | 4 (4) | 2.250 | +0.057 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 4 (4) | 2.480 | +1.013 | 1.583 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 39 (3) | 0.702 | +0.176 | — | — |  |
| hits | pf-mlb:F (challenger) | 72 (4) | 0.634 | -0.037 | 0.409 | 83% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 39 (3) | 1.279 | +0.070 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 72 (4) | 1.154 | +0.041 | 0.762 | 89% | — |
| home_runs | pf-mlb:F (challenger) | 72 (4) | 0.150 | +0.051 | 0.055 | 81% | — |
| outs | incumbent_pitcher_champion (incumbent) | 8 (4) | 3.663 | +2.357 | — | — |  |
| outs | pf-mlb:F (challenger) | 8 (4) | 3.671 | +1.834 | 2.649 | 62% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 8 (4) | 1.473 | +1.047 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 8 (4) | 1.552 | +1.150 | 0.955 | 88% | — |
| plate_appearances | pf-mlb:F (challenger) | 72 (4) | 0.448 | -0.052 | 0.260 | 85% | — |
| rbis | incumbent_hitter_engine (incumbent) | 38 (3) | 0.505 | -0.052 | — | — |  |
| rbis | pf-mlb:F (challenger) | 72 (4) | 0.522 | +0.030 | 0.276 | 85% | — |
| runs | pf-mlb:F (challenger) | 72 (4) | 0.485 | +0.052 | 0.244 | 81% | — |
| team_runs | incumbent_team_runs (incumbent) | 8 (4) | 1.359 | +0.028 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 8 (4) | 1.461 | +0.506 | 0.994 | 100% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 39 (3) | 1.060 | +0.285 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 72 (4) | 0.993 | +0.112 | 0.648 | 86% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 8 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 4 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 72 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 72 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 72 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 8 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 8 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 72 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 72 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 72 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 8 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 72 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 7 | 3.6904 | 1.1004 | 7 | 3.4071 |
| short_rest(<=4d) | 2 | 3.1401 | -2.9424 | 2 | 2.9243 |
| normal_rest(>4d) | 6 | 3.2917 | 2.2975 | 6 | 3.6845 |
| early_exit(actual BF<=12) | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 7 | 3.6904 | 1.1004 | 7 | 3.4071 |
| starter_CONFIRMED | 8 | 3.2538 | 0.9875 | 8 | 3.4945 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 7 | 3.9043 | 1.805 | 7 | 3.7843 |
| short_rest(<=4d) | 2 | 2.1838 | -0.1487 | 2 | 1.7019 |
| normal_rest(>4d) | 6 | 4.1663 | 2.4946 | 6 | 4.3167 |
| early_exit(actual BF<=12) | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 7 | 3.9043 | 1.805 | 7 | 3.7843 |
| starter_CONFIRMED | 8 | 3.6706 | 1.8337 | 8 | 3.663 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 7 | 1.5038 | 1.0448 | 7 | 1.4038 |
| short_rest(<=4d) | 2 | 1.4745 | 1.4745 | 2 | 1.5944 |
| normal_rest(>4d) | 6 | 1.5779 | 1.0424 | 6 | 1.433 |
| early_exit(actual BF<=12) | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 7 | 1.5038 | 1.0448 | 7 | 1.4038 |
| starter_CONFIRMED | 8 | 1.5521 | 1.1504 | 8 | 1.4734 |

**Participation:** pitcher forecasts resolved 10; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
