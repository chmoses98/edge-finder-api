## MLB projection scorecard (weekly) — last 28 days

Generated 2026-10-27T06:21:39Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1007 across 6 games. Settled: 875 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 10 (5) | 3.165 | +2.035 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 10 (5) | 2.754 | +0.757 | 2.100 | 80% | — |
| game_total | incumbent_team_runs (incumbent) | 5 (5) | 2.334 | -0.489 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 5 (5) | 2.566 | +0.229 | 1.666 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 39 (3) | 0.702 | +0.176 | — | — |  |
| hits | pf-mlb:F (challenger) | 90 (5) | 0.698 | -0.058 | 0.452 | 82% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 39 (3) | 1.279 | +0.070 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 90 (5) | 1.308 | -0.054 | 0.850 | 87% | — |
| home_runs | pf-mlb:F (challenger) | 90 (5) | 0.141 | +0.063 | 0.047 | 82% | — |
| outs | incumbent_pitcher_champion (incumbent) | 10 (5) | 3.383 | +1.715 | — | — |  |
| outs | pf-mlb:F (challenger) | 10 (5) | 3.398 | +1.216 | 2.429 | 70% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 10 (5) | 1.312 | +0.971 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 10 (5) | 1.413 | +1.092 | 0.878 | 90% | — |
| plate_appearances | pf-mlb:F (challenger) | 90 (5) | 0.517 | +0.011 | 0.315 | 80% | — |
| rbis | incumbent_hitter_engine (incumbent) | 38 (3) | 0.505 | -0.052 | — | — |  |
| rbis | pf-mlb:F (challenger) | 90 (5) | 0.534 | -0.013 | 0.297 | 84% | — |
| runs | pf-mlb:F (challenger) | 90 (5) | 0.491 | +0.022 | 0.253 | 82% | — |
| team_runs | incumbent_team_runs (incumbent) | 10 (5) | 1.947 | -0.244 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 10 (5) | 2.037 | +0.114 | 1.379 | 90% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 39 (3) | 1.060 | +0.285 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 90 (5) | 1.041 | +0.104 | 0.667 | 87% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 10 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 5 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 90 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 90 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 90 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 10 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 10 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 90 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 90 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 90 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 10 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 90 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 9 | 3.0375 | 0.8192 | 9 | 3.0608 |
| short_rest(<=4d) | 2 | 3.1401 | -2.9424 | 2 | 2.9243 |
| normal_rest(>4d) | 8 | 2.6569 | 1.6819 | 8 | 3.2255 |
| early_exit(actual BF<=12) | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 9 | 3.0375 | 0.8192 | 9 | 3.0608 |
| starter_CONFIRMED | 10 | 2.7535 | 0.757 | 10 | 3.1653 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 9 | 3.5492 | 1.1247 | 9 | 3.4461 |
| short_rest(<=4d) | 2 | 2.1838 | -0.1487 | 2 | 1.7019 |
| normal_rest(>4d) | 8 | 3.7012 | 1.5569 | 8 | 3.8032 |
| early_exit(actual BF<=12) | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 9 | 3.5492 | 1.1247 | 9 | 3.4461 |
| starter_CONFIRMED | 10 | 3.3978 | 1.2157 | 10 | 3.3829 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 9 | 1.3603 | 1.0033 | 9 | 1.2404 |
| short_rest(<=4d) | 2 | 1.4745 | 1.4745 | 2 | 1.5944 |
| normal_rest(>4d) | 8 | 1.3979 | 0.9963 | 8 | 1.242 |
| early_exit(actual BF<=12) | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 9 | 1.3603 | 1.0033 | 9 | 1.2404 |
| starter_CONFIRMED | 10 | 1.4133 | 1.0919 | 10 | 1.3125 |

**Participation:** pitcher forecasts resolved 12; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
