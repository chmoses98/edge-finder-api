## MLB projection scorecard (weekly) — last 28 days

Generated 2026-10-23T22:14:06Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 650 across 3 games. Settled: 356 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 4 (2) | 4.121 | +1.666 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 4 (2) | 4.403 | -0.129 | 3.276 | 50% | — |
| game_total | incumbent_team_runs (incumbent) | 2 (2) | 2.172 | +0.109 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 2 (2) | 2.829 | +0.886 | 1.735 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 17 (1) | 0.733 | +0.311 | — | — |  |
| hits | pf-mlb:F (challenger) | 36 (2) | 0.659 | -0.005 | 0.416 | 81% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 17 (1) | 1.467 | -0.007 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 36 (2) | 1.236 | +0.052 | 0.799 | 86% | — |
| home_runs | pf-mlb:F (challenger) | 36 (2) | 0.143 | +0.048 | 0.051 | 81% | — |
| outs | incumbent_pitcher_champion (incumbent) | 4 (2) | 4.189 | +1.646 | — | — |  |
| outs | pf-mlb:F (challenger) | 4 (2) | 4.229 | +1.138 | 2.913 | 75% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 4 (2) | 1.329 | +1.136 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 4 (2) | 1.361 | +1.136 | 0.838 | 75% | — |
| plate_appearances | pf-mlb:F (challenger) | 36 (2) | 0.452 | -0.103 | 0.261 | 81% | — |
| rbis | incumbent_hitter_engine (incumbent) | 17 (1) | 0.628 | -0.190 | — | — |  |
| rbis | pf-mlb:F (challenger) | 36 (2) | 0.524 | +0.036 | 0.270 | 83% | — |
| runs | pf-mlb:F (challenger) | 36 (2) | 0.513 | +0.027 | 0.266 | 72% | — |
| team_runs | incumbent_team_runs (incumbent) | 4 (2) | 1.554 | +0.054 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 4 (2) | 1.856 | +0.443 | 1.159 | 100% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 17 (1) | 1.319 | +0.232 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 36 (2) | 1.047 | +0.151 | 0.642 | 86% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 4 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 2 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 36 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 36 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 36 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 4 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 4 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 36 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 36 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 36 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 4 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 36 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 3 | 5.805 | -0.2383 | 3 | 4.1262 |
| short_rest(<=4d) | 2 | 3.1401 | -2.9424 | 2 | 2.9243 |
| normal_rest(>4d) | 2 | 5.6663 | 2.6838 | 2 | 5.3178 |
| early_exit(actual BF<=12) | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 3 | 5.805 | -0.2383 | 3 | 4.1262 |
| starter_CONFIRMED | 4 | 4.4032 | -0.1293 | 4 | 4.1211 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 3 | 4.96 | 0.8383 | 3 | 4.6476 |
| short_rest(<=4d) | 2 | 2.1838 | -0.1487 | 2 | 1.7019 |
| normal_rest(>4d) | 2 | 6.2738 | 2.4237 | 2 | 6.6764 |
| early_exit(actual BF<=12) | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 3 | 4.96 | 0.8383 | 3 | 4.6476 |
| starter_CONFIRMED | 4 | 4.2288 | 1.1375 | 4 | 4.1891 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 3 | 1.1852 | 0.8844 | 3 | 1.1184 |
| short_rest(<=4d) | 2 | 1.4745 | 1.4745 | 2 | 1.5944 |
| normal_rest(>4d) | 2 | 1.2482 | 0.797 | 2 | 1.0636 |
| early_exit(actual BF<=12) | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 3 | 1.1852 | 0.8844 | 3 | 1.1184 |
| starter_CONFIRMED | 4 | 1.3613 | 1.1358 | 4 | 1.329 |

**Participation:** pitcher forecasts resolved 6; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
