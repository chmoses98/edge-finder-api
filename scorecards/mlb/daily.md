## MLB projection scorecard (daily) — last 7 days

Generated 2026-10-28T23:13:12Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1372 across 7 games. Settled: 1013 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 7 (4) | 2.270 | +1.852 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 8 (4) | 1.439 | +0.812 | 1.218 | 100% | — |
| game_total | incumbent_team_runs (incumbent) | 3 (3) | 2.442 | -0.887 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 4 (4) | 2.152 | +0.203 | 1.475 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 22 (2) | 0.678 | +0.072 | — | — |  |
| hits | pf-mlb:F (challenger) | 72 (4) | 0.756 | -0.073 | 0.498 | 74% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 22 (2) | 1.134 | +0.130 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 72 (4) | 1.414 | -0.012 | 0.913 | 83% | — |
| home_runs | pf-mlb:F (challenger) | 72 (4) | 0.158 | +0.055 | 0.060 | 79% | — |
| outs | incumbent_pitcher_champion (incumbent) | 7 (4) | 2.531 | +1.602 | — | — |  |
| outs | pf-mlb:F (challenger) | 8 (4) | 2.339 | +1.157 | 1.821 | 75% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 7 (4) | 1.348 | +0.506 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 8 (4) | 1.266 | +0.626 | 0.847 | 88% | — |
| plate_appearances | pf-mlb:F (challenger) | 72 (4) | 0.682 | -0.116 | 0.449 | 71% | — |
| rbis | incumbent_hitter_engine (incumbent) | 21 (2) | 0.406 | +0.059 | — | — |  |
| rbis | pf-mlb:F (challenger) | 72 (4) | 0.544 | +0.006 | 0.300 | 86% | — |
| runs | pf-mlb:F (challenger) | 72 (4) | 0.484 | +0.059 | 0.243 | 83% | — |
| team_runs | incumbent_team_runs (incumbent) | 6 (3) | 2.208 | -0.443 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 8 (4) | 1.798 | +0.101 | 1.329 | 88% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 22 (2) | 0.860 | +0.327 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 72 (4) | 1.170 | +0.055 | 0.766 | 78% | — |

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
| opener_flag=0 | 8 | 1.4394 | 0.8119 | 7 | 2.2698 |
| short_rest(<=4d) | 1 | 1.2624 | -1.2624 | 0 | — |
| normal_rest(>4d) | 7 | 1.4647 | 1.1082 | 7 | 2.2698 |
| early_exit(actual BF<=12) | 1 | 1.2624 | -1.2624 | 0 | — |
| went_deeper(actual BF>12) | 7 | 1.4647 | 1.1082 | 7 | 2.2698 |
| starter_CONFIRMED | 8 | 1.4394 | 0.8119 | 7 | 2.2698 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 8 | 2.3388 | 1.1569 | 7 | 2.531 |
| short_rest(<=4d) | 1 | 0.2925 | 0.2925 | 0 | — |
| normal_rest(>4d) | 7 | 2.6311 | 1.2803 | 7 | 2.531 |
| early_exit(actual BF<=12) | 1 | 0.2925 | 0.2925 | 0 | — |
| went_deeper(actual BF>12) | 7 | 2.6311 | 1.2803 | 7 | 2.531 |
| starter_CONFIRMED | 8 | 2.3388 | 1.1569 | 7 | 2.531 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 8 | 1.2664 | 0.6255 | 7 | 1.3482 |
| short_rest(<=4d) | 1 | 0.0362 | 0.0362 | 0 | — |
| normal_rest(>4d) | 7 | 1.4422 | 0.7097 | 7 | 1.3482 |
| early_exit(actual BF<=12) | 1 | 0.0362 | 0.0362 | 0 | — |
| went_deeper(actual BF>12) | 7 | 1.4422 | 0.7097 | 7 | 1.3482 |
| starter_CONFIRMED | 8 | 1.2664 | 0.6255 | 7 | 1.3482 |

**Participation:** pitcher forecasts resolved 14; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
