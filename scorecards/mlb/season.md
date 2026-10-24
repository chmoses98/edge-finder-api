## MLB projection scorecard (season) — season to date

Generated 2026-10-24T21:55:16Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 854 across 4 games. Settled: 543 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 6 (3) | 3.236 | +1.600 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 6 (3) | 3.300 | +0.278 | 2.531 | 67% | — |
| game_total | incumbent_team_runs (incumbent) | 3 (3) | 2.225 | +0.850 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 3 (3) | 2.976 | +1.681 | 1.720 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 28 (2) | 0.792 | +0.271 | — | — |  |
| hits | pf-mlb:F (challenger) | 54 (3) | 0.675 | +0.001 | 0.424 | 80% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 28 (2) | 1.447 | +0.189 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 54 (3) | 1.206 | +0.155 | 0.765 | 87% | — |
| home_runs | pf-mlb:F (challenger) | 54 (3) | 0.132 | +0.069 | 0.038 | 83% | — |
| outs | incumbent_pitcher_champion (incumbent) | 6 (3) | 2.819 | +1.078 | — | — |  |
| outs | pf-mlb:F (challenger) | 6 (3) | 3.013 | +0.564 | 2.189 | 83% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 6 (3) | 1.525 | +0.956 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 6 (3) | 1.609 | +1.074 | 0.996 | 83% | — |
| plate_appearances | pf-mlb:F (challenger) | 54 (3) | 0.428 | +0.027 | 0.246 | 85% | — |
| rbis | incumbent_hitter_engine (incumbent) | 27 (2) | 0.534 | -0.066 | — | — |  |
| rbis | pf-mlb:F (challenger) | 54 (3) | 0.508 | +0.068 | 0.254 | 85% | — |
| runs | pf-mlb:F (challenger) | 54 (3) | 0.487 | +0.089 | 0.238 | 80% | — |
| team_runs | incumbent_team_runs (incumbent) | 6 (3) | 1.425 | +0.425 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 6 (3) | 1.783 | +0.841 | 1.052 | 100% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 28 (2) | 1.201 | +0.358 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 54 (3) | 1.004 | +0.198 | 0.621 | 87% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 6 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 3 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 54 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 54 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 54 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 6 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 6 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 54 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 54 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 54 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 6 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 54 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 5 | 3.92 | 0.294 | 5 | 3.0622 |
| short_rest(<=4d) | 2 | 3.1401 | -2.9424 | 2 | 2.9243 |
| normal_rest(>4d) | 4 | 3.3794 | 1.8881 | 4 | 3.392 |
| early_exit(actual BF<=12) | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 5 | 3.92 | 0.294 | 5 | 3.0622 |
| starter_CONFIRMED | 6 | 3.2996 | 0.278 | 6 | 3.2361 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 5 | 3.209 | 0.27 | 5 | 2.8202 |
| short_rest(<=4d) | 2 | 2.1838 | -0.1487 | 2 | 1.7019 |
| normal_rest(>4d) | 4 | 3.4281 | 0.9206 | 4 | 3.3778 |
| early_exit(actual BF<=12) | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 5 | 3.209 | 0.27 | 5 | 2.8202 |
| starter_CONFIRMED | 6 | 3.0133 | 0.5642 | 6 | 2.8191 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 5 | 1.5529 | 0.9103 | 5 | 1.4381 |
| short_rest(<=4d) | 2 | 1.4745 | 1.4745 | 2 | 1.5944 |
| normal_rest(>4d) | 4 | 1.6763 | 0.873 | 4 | 1.4906 |
| early_exit(actual BF<=12) | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 5 | 1.5529 | 0.9103 | 5 | 1.4381 |
| starter_CONFIRMED | 6 | 1.6091 | 1.0735 | 6 | 1.5252 |

**Participation:** pitcher forecasts resolved 8; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
