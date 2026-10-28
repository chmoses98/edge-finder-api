## MLB projection scorecard (weekly) — last 28 days

Generated 2026-10-28T06:36:23Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1148 across 7 games. Settled: 1013 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 11 (6) | 2.943 | +1.784 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 12 (6) | 2.427 | +0.498 | 1.904 | 83% | — |
| game_total | incumbent_team_runs (incumbent) | 5 (5) | 2.334 | -0.489 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 6 (6) | 2.378 | +0.431 | 1.562 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 39 (3) | 0.702 | +0.176 | — | — |  |
| hits | pf-mlb:F (challenger) | 108 (6) | 0.723 | -0.050 | 0.470 | 81% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 39 (3) | 1.279 | +0.070 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 108 (6) | 1.354 | +0.009 | 0.875 | 85% | — |
| home_runs | pf-mlb:F (challenger) | 108 (6) | 0.153 | +0.053 | 0.057 | 81% | — |
| outs | incumbent_pitcher_champion (incumbent) | 11 (6) | 3.134 | +1.618 | — | — |  |
| outs | pf-mlb:F (challenger) | 12 (6) | 2.969 | +1.150 | 2.185 | 75% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 11 (6) | 1.341 | +0.735 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 12 (6) | 1.298 | +0.796 | 0.844 | 92% | — |
| plate_appearances | pf-mlb:F (challenger) | 108 (6) | 0.605 | -0.112 | 0.386 | 69% | — |
| rbis | incumbent_hitter_engine (incumbent) | 38 (3) | 0.505 | -0.052 | — | — |  |
| rbis | pf-mlb:F (challenger) | 108 (6) | 0.537 | +0.016 | 0.290 | 84% | — |
| runs | pf-mlb:F (challenger) | 108 (6) | 0.494 | +0.049 | 0.250 | 81% | — |
| team_runs | incumbent_team_runs (incumbent) | 10 (5) | 1.947 | -0.244 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 12 (6) | 1.818 | +0.215 | 1.272 | 92% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 39 (3) | 1.060 | +0.285 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 108 (6) | 1.129 | +0.087 | 0.725 | 84% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 12 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 6 settled forecasts (< 100)
- hits: **OK** — within expectations
- hits_runs_rbis: **OK** — within expectations
- home_runs: **ALERT** — bias +0.053 (95% CI +0.015..+0.092) exceeds 5% of the mean
- outs: **INSUFFICIENT** — 12 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 12 settled forecasts (< 100)
- plate_appearances: **OK** — within expectations
- rbis: **OK** — within expectations
- runs: **OK** — within expectations
- team_runs: **INSUFFICIENT** — 12 settled forecasts (< 100)
- total_bases: **OK** — within expectations

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| opener_flag=0 | 11 | 2.63 | 0.5255 | 10 | 2.8267 |
| short_rest(<=4d) | 3 | 2.5142 | -2.3824 | 2 | 2.9243 |
| normal_rest(>4d) | 9 | 2.3983 | 1.4584 | 9 | 2.9472 |
| early_exit(actual BF<=12) | 2 | 0.73 | -0.5323 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 10 | 2.7668 | 0.7043 | 10 | 2.8267 |
| starter_CONFIRMED | 12 | 2.4273 | 0.4982 | 11 | 2.943 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 11 | 3.0536 | 1.07 | 10 | 3.166 |
| short_rest(<=4d) | 3 | 1.5534 | -0.0016 | 2 | 1.7019 |
| normal_rest(>4d) | 9 | 3.4406 | 1.5344 | 9 | 3.4522 |
| early_exit(actual BF<=12) | 2 | 1.1638 | 1.1638 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 10 | 3.3297 | 1.1477 | 10 | 3.166 |
| starter_CONFIRMED | 12 | 2.9688 | 1.1504 | 11 | 3.134 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 11 | 1.2443 | 0.6961 | 10 | 1.2792 |
| short_rest(<=4d) | 3 | 0.9951 | 0.9951 | 2 | 1.5944 |
| normal_rest(>4d) | 9 | 1.3991 | 0.7291 | 9 | 1.2849 |
| early_exit(actual BF<=12) | 2 | 0.963 | 0.963 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 10 | 1.3651 | 0.7621 | 10 | 1.2792 |
| starter_CONFIRMED | 12 | 1.2981 | 0.7956 | 11 | 1.3412 |

**Participation:** pitcher forecasts resolved 14; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
