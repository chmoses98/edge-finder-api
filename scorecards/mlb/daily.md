## MLB projection scorecard (daily) — last 7 days

Generated 2026-10-26T20:52:17Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1004 across 5 games. Settled: 731 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 6 (3) | 2.887 | +2.306 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 6 (3) | 2.450 | +0.422 | 1.846 | 83% | — |
| game_total | incumbent_team_runs (incumbent) | 3 (3) | 2.239 | -0.685 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 3 (3) | 2.069 | +0.112 | 1.459 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 39 (3) | 0.702 | +0.176 | — | — |  |
| hits | pf-mlb:F (challenger) | 54 (3) | 0.621 | -0.018 | 0.404 | 85% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 39 (3) | 1.279 | +0.070 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 54 (3) | 1.176 | -0.018 | 0.793 | 93% | — |
| home_runs | pf-mlb:F (challenger) | 54 (3) | 0.166 | +0.035 | 0.070 | 80% | — |
| outs | incumbent_pitcher_champion (incumbent) | 6 (3) | 2.659 | +2.416 | — | — |  |
| outs | pf-mlb:F (challenger) | 6 (3) | 2.803 | +1.637 | 2.021 | 67% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 6 (3) | 1.610 | +1.170 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 6 (3) | 1.653 | +1.268 | 0.985 | 83% | — |
| plate_appearances | pf-mlb:F (challenger) | 54 (3) | 0.454 | -0.107 | 0.262 | 83% | — |
| rbis | incumbent_hitter_engine (incumbent) | 38 (3) | 0.505 | -0.052 | — | — |  |
| rbis | pf-mlb:F (challenger) | 54 (3) | 0.554 | -0.015 | 0.309 | 80% | — |
| runs | pf-mlb:F (challenger) | 54 (3) | 0.504 | +0.009 | 0.265 | 83% | — |
| team_runs | incumbent_team_runs (incumbent) | 6 (3) | 1.432 | -0.342 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 6 (3) | 1.329 | +0.056 | 0.988 | 100% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 39 (3) | 1.060 | +0.285 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 54 (3) | 1.026 | +0.071 | 0.682 | 83% | — |

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
| opener_flag=0 | 5 | 2.9 | 0.467 | 5 | 2.6429 |
| short_rest(<=4d) | 2 | 3.1401 | -2.9424 | 2 | 2.9243 |
| normal_rest(>4d) | 4 | 2.1044 | 2.1044 | 4 | 2.8678 |
| early_exit(actual BF<=12) | 1 | 0.1977 | 0.1977 | 1 | 4.1057 |
| went_deeper(actual BF>12) | 5 | 2.9 | 0.467 | 5 | 2.6429 |
| starter_CONFIRMED | 6 | 2.4496 | 0.4221 | 6 | 2.8867 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| opener_flag=0 | 5 | 2.9565 | 1.5575 | 5 | 2.6275 |
| short_rest(<=4d) | 2 | 2.1838 | -0.1487 | 2 | 1.7019 |
| normal_rest(>4d) | 4 | 3.1125 | 2.53 | 4 | 3.1369 |
| early_exit(actual BF<=12) | 1 | 2.0351 | 2.0351 | 1 | 2.8138 |
| went_deeper(actual BF>12) | 5 | 2.9565 | 1.5575 | 5 | 2.6275 |
| starter_CONFIRMED | 6 | 2.8029 | 1.6371 | 6 | 2.6586 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=1 | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| opener_flag=0 | 5 | 1.6061 | 1.1439 | 5 | 1.5399 |
| short_rest(<=4d) | 2 | 1.4745 | 1.4745 | 2 | 1.5944 |
| normal_rest(>4d) | 4 | 1.7428 | 1.1651 | 4 | 1.6178 |
| early_exit(actual BF<=12) | 1 | 1.8899 | 1.8899 | 1 | 1.9606 |
| went_deeper(actual BF>12) | 5 | 1.6061 | 1.1439 | 5 | 1.5399 |
| starter_CONFIRMED | 6 | 1.6534 | 1.2682 | 6 | 1.61 |

**Participation:** pitcher forecasts resolved 10; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
